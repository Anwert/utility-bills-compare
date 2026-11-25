"""Yerevan gas, electricity, water, waste and maintenance tariffs.

PSRC tariff sections are empty without JavaScript, so collection starts from
``render_html`` (a saved DOM in tests). The consumer PDF is the link whose
text is the tariff table, not a yearly review slide. Armenian row labels and
the effective dates are not in the PDF text layer: gas and electricity are read
by position, and the dates come from ``settings.effective``.

The drinking-water decision is a scan. Its prices are copied by hand for one
year, and any other month is a failed source, so the policy keeps the last value
as stale and opens an alert until the next decision is copied.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.schemas import (
    ITEM_CATEGORY,
    ExtractionMethod,
    Item,
    NormalizedRecord,
    SourceConfig,
    SourceInfo,
    Tier,
)

_GAS_PAIR = re.compile(r"(?m)([\d,]+\.\d{2})[ \t]+([\d,]+\.\d{2})[ \t]*$")
_GAS_LABEL = re.compile(r"(?m)^[ \t]*(\d\.\d)(?!\d)")
# Rows of the gas table that carry two prices, and the labels the text layer keeps.
# Row 2.2 has two sub-rows, «ա» and «բ», whose labels it loses.
_GAS_ROWS = ("1.1", "1.2", "2.1", "2.2ա", "2.2բ", "3", "4.1", "4.2", "5.1", "5.2")
# Priced in drams per 1000 m³; the other rows are in dollars, hundreds at most.
_GAS_DRAM_ROWS = {"1.1", "1.2", "2.2ա", "5.1"}
_GAS_LABELS = ["1.1", "1.2", "2.1", "2.2", "4.1", "4.2", "5.1", "5.2"]
_CREATED = re.compile(rb"CreationDate\(D:(\d{4})(\d{2})(\d{2})")
_RENDER_BUDGETS_MS = (15_000, 30_000, 60_000)
_ACT_YEAR = re.compile(r"ՏԵՂԱԿԱՆ ՎՃԱՐՆԵՐԻ (\d{4}) ԹՎԱԿԱՆԻ")
_WASTE_FEE = re.compile(r"20\. Երևանի կողմից աղբահանության.{0,800}?(\d+(?:[.,]\d+)?) /[^/]+/ դրամ")
_MAINTENANCE_FEE = re.compile(
    r"22\. Երևանի կողմից կառավարվող.{0,500}?(\d+(?:[.,]\d+)?) /[^/]+/ դրամ"
)

# Household 0.38 kV steps. ``up_to`` is exclusive, so 201 means "through 200 kWh".
_STEP_BOUNDS: tuple[tuple[float | None, str], ...] = (
    (201, "up to 200 kWh/month"),
    (401, "201-400 kWh/month"),
    (None, "above 400 kWh/month"),
)

# Decision 397-N of 26.11.2025, rows 1.1 and 1.2, ordinary subscribers, with VAT.
# The PDF has no text layer: for the next year copy its decision and move the year.
_RECORDED_WATER_YEAR = 2026
_RECORDED_WATER: dict[str, tuple[Item, float, str]] = {
    "water": (Item.water, 170.36, "питьевая вода"),
    "sewage": (Item.sewage, 30.06, "водоотведение"),
}


class _PdfLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._chunks: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if ".pdf" in href.lower():
            self._href = href
            self._chunks = []
        else:
            self._href = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._chunks)).strip()
            self.links.append((self._href, text))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._chunks.append(data)


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _require_url(source: SourceConfig) -> str:
    url = source.settings.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError(f"{source.adapter} requires settings.url")
    return url


def _absolute(page_url: str, href: str) -> str:
    if href.startswith(("https://", "http://")):
        return href
    parts = urlsplit(page_url)
    path = href if href.startswith("/") else f"/{href}"
    return f"{parts.scheme}://{parts.netloc}{path}"


def _pdf_links(html: str) -> list[tuple[str, str]]:
    parser = _PdfLinks()
    parser.feed(html)
    return parser.links


def _pick_pdf(ctx: CollectContext, page_url: str, predicate) -> str:
    """The tariff PDF linked on a page that fills its list of files by script.

    On a slow connection Chrome prints the DOM before the list arrives, so a page
    without the link is rendered again with more time.
    """

    for budget_ms in _RENDER_BUDGETS_MS:
        html = ctx.fetcher.render_html(page_url, budget_ms=budget_ms)
        for href, text in _pdf_links(html):
            if predicate(text):
                return _absolute(page_url, href)
    raise ValueError(f"no consumer tariff PDF on {page_url}")


def _is_gas_tariff(text: str) -> bool:
    return "բնական գազի սակագները" in text and "վերանայման" not in text


def _is_electricity_tariff(text: str) -> bool:
    return "էլեկտրական էներգիայի սակագները" in text and "վերանայման" not in text


def _is_drinking_water(text: str) -> bool:
    return text.strip() == "Խմելու ջուր"


def _created_on(pdf: bytes) -> date:
    match = _CREATED.search(pdf)
    if match is None:
        raise ValueError("PDF has no CreationDate")
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def _effective(
    ctx: CollectContext, source: SourceConfig, pdf_url: str, prices: list[float]
) -> tuple[date, str]:
    """When these prices came into force, and the sentence that says so.

    The PDF prints the date outside its text layer. ``settings.effective`` holds the
    dates read by eye, each tied to exact prices; a price nobody has checked yet is
    dated by the PDF itself.
    """

    for entry in _recorded_dates(source):
        recorded, valid_from, basis = entry
        if recorded == prices:
            return valid_from, f"Действует с {valid_from:%d.%m.%Y}: {basis}."
    created = _created_on(ctx.fetcher.fetch_bytes(pdf_url))
    return created, (
        "Дата начала действия в тексте документа не указана: "
        f"взята дата создания файла, {created:%d.%m.%Y}."
    )


def _recorded_dates(source: SourceConfig) -> list[tuple[list[float], date, str]]:
    entries = source.settings.get("effective", [])
    if not isinstance(entries, list):
        raise ValueError(f"{source.adapter}: settings.effective must be a list")
    return [_recorded_date(source, entry) for entry in entries]


def _recorded_date(source: SourceConfig, entry: object) -> tuple[list[float], date, str]:
    bad = f"{source.adapter}: settings.effective needs prices, valid_from and basis, got {entry!r}"
    if not isinstance(entry, dict):
        raise ValueError(bad)
    prices, start, basis = (entry.get(key) for key in ("prices", "valid_from", "basis"))
    if not isinstance(prices, list) or not prices:
        raise ValueError(bad)
    if any(isinstance(price, bool) or not isinstance(price, int | float) for price in prices):
        raise ValueError(bad)
    if not isinstance(start, str) or not isinstance(basis, str) or not basis:
        raise ValueError(bad)
    try:
        valid_from = date.fromisoformat(start)
    except ValueError as exc:
        raise ValueError(bad) from exc
    return [float(price) for price in prices], valid_from, basis


def _household_gas(text: str) -> float:
    """AMD per 1000 m³ with VAT from row 5.1, consumers of up to 10,000 m³ a month.

    Rows 1.1 and 1.2 are for socially vulnerable families; an ordinary household is
    one of the "other consumers" of row 5. Row labels and numbers sit on different
    baselines, so a label lands beside its own pair with poppler but beside another
    one with xpdf. The pairs keep the order of the rows in both, so the price is read
    by place, and only from a table of exactly the rows known here.
    """

    if _GAS_LABEL.findall(text) != _GAS_LABELS:
        raise ValueError("gas PDF does not have the known rows")
    pairs = [
        (float(net.replace(",", "")), float(gross.replace(",", "")))
        for net, gross in _GAS_PAIR.findall(text)
    ]
    if len(pairs) != len(_GAS_ROWS):
        raise ValueError(f"gas PDF has {len(pairs)} priced rows, not {len(_GAS_ROWS)}")
    for row, (net, gross) in zip(_GAS_ROWS, pairs, strict=True):
        if abs(net * 1.2 - gross) > 1 or (gross >= 10_000) != (row in _GAS_DRAM_ROWS):
            raise ValueError(f"gas PDF row {row} is not its currency with 20% VAT: {net}, {gross}")
    price = pairs[_GAS_ROWS.index("5.1")][1]
    if price > 1_000_000:
        raise ValueError(f"gas PDF row 5.1 is not AMD per 1000 m³: {price}")
    return price


def _table_rows(text: str) -> dict[int, list[float]]:
    rows: dict[int, list[float]] = {}
    current: int | None = None
    for raw in text.splitlines():
        line = raw.replace("\x0c", "").strip()
        if not line or line == "№":
            continue
        if re.fullmatch(r"\d{1,2}", line):
            current = int(line)
            rows.setdefault(current, [])
            continue
        if current is not None and re.fullmatch(r"\d+\.\d+", line):
            rows[current].append(float(line))
    return rows


def _steps(prices: list[float], when: str) -> list[Tier]:
    return [
        Tier(up_to=bound, value=price, label=f"{when}, {label}")
        for (bound, label), price in zip(_STEP_BOUNDS, prices, strict=True)
    ]


def _number(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _by_steps(prices: list[float]) -> str:
    low, middle, high = (_number(price) for price in prices)
    return f"{low} при расходе до 200 кВт·ч в месяц, {middle} при 201–400, {high} выше 400"


def _record(
    ctx: CollectContext,
    source: SourceConfig,
    *,
    item: Item,
    value: float,
    unit: str,
    valid_from: date,
    url: str,
    note: str,
    valid_to: date | None = None,
    tiers: list[Tier] | None = None,
    fetched_at: datetime | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        city=ctx.city.id,
        item=item,
        category=ITEM_CATEGORY[item],
        value=value,
        unit=unit,
        currency=ctx.city.currency,
        tiers=tiers or [],
        valid_from=valid_from,
        valid_to=valid_to,
        source=SourceInfo(
            kind=source.kind,
            url=url,
            fetched_at=fetched_at or datetime.now(UTC),
            name=source.id,
        ),
        method=ExtractionMethod.regex,
        reliability=source.reliability,
        note=note,
    )


def _plain(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    return re.sub(r"\s+", " ", "".join(parser.parts))


class YerevanPsrcGas:
    """Household natural gas, row 5.1, column with VAT, AMD per m³."""

    name = "yerevan_psrc_gas"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        page = _require_url(source)
        pdf_url = _pick_pdf(ctx, page, _is_gas_tariff)
        per_thousand = _household_gas(ctx.fetcher.pdf_to_text(pdf_url))
        valid_from, dated = _effective(ctx, source, pdf_url, [per_thousand])
        value = per_thousand / 1000
        note = (
            f"{_number(value)} AMD за м³ с НДС — цена для обычных потребителей до 10 000 м³ "
            f"в месяц (строка 5.1 таблицы КРОУ). {dated}"
        )
        record = _record(
            ctx,
            source,
            item=Item.gas,
            value=value,
            unit="AMD/m3",
            valid_from=valid_from,
            url=pdf_url,
            note=note,
        )
        return AdapterOutput(records=[record])


class YerevanPsrcElectricity:
    """Household 0.38 kV day and night steps. Rows 2–4, not the vulnerable row."""

    name = "yerevan_psrc_electricity"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        page = _require_url(source)
        pdf_url = _pick_pdf(ctx, page, _is_electricity_tariff)
        rows = _table_rows(ctx.fetcher.pdf_to_text(pdf_url))
        try:
            day_prices = [rows[row][0] for row in (2, 3, 4)]
            night_prices = [rows[row][1] for row in (2, 3, 4)]
        except (KeyError, IndexError) as exc:
            raise ValueError("electricity PDF has no household day/night steps") from exc
        valid_from, dated = _effective(ctx, source, pdf_url, [*day_prices, *night_prices])
        # One timestamp: of two records equally fresh the calculation takes the
        # first, so a cell is billed at the day price on every machine.
        fetched_at = datetime.now(UTC)
        day = _record(
            ctx,
            source,
            item=Item.electricity,
            value=day_prices[0],
            unit="AMD/kWh",
            valid_from=valid_from,
            url=pdf_url,
            tiers=_steps(day_prices, "day"),
            fetched_at=fetched_at,
            note=(
                f"Дневной тариф для населения, AMD за кВт·ч с НДС: {_by_steps(day_prices)}. "
                "Весь расход посчитан по дневной цене: какая его часть приходится на ночь, "
                f"неизвестно. {dated}"
            ),
        )
        night = _record(
            ctx,
            source,
            item=Item.electricity,
            value=night_prices[0],
            unit="AMD/kWh",
            valid_from=valid_from,
            url=pdf_url,
            tiers=_steps(night_prices, "night"),
            fetched_at=fetched_at,
            note=(
                f"Ночной тариф для населения, AMD за кВт·ч с НДС: {_by_steps(night_prices)}. "
                f"В счёт не входит. {dated}"
            ),
        )
        return AdapterOutput(records=[day, night])


class YerevanPsrcScan:
    """Official water decision. Disabled: the PDF is a scan and cannot be read."""

    name = "yerevan_psrc_scan"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        page = _require_url(source)
        pdf_url = _pick_pdf(ctx, page, _is_drinking_water)
        ctx.fetcher.pdf_to_text(pdf_url)
        raise ValueError("PSRC water decision is a scan without a text layer")


class YerevanArlis:
    """One yearly council decision: waste (point 20) or the mayor's minimum (point 22)."""

    name = "yerevan_arlis"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        url = _require_url(source)
        text = _plain(ctx.fetcher.fetch_text(url))
        year_match = _ACT_YEAR.search(text)
        if year_match is None:
            raise ValueError("arlis act has no local-fee year in the title")
        year = int(year_match.group(1))
        item_name = source.settings.get("item")
        if item_name == "waste":
            found = _WASTE_FEE.search(text)
            item = Item.waste
            point = "20"
            caveat = (
                "Периода в решении нет, плата считается помесячно по закону "
                "о вывозе мусора HO-237-N (статьи 15 и 18)."
            )
        elif item_name == "maintenance":
            found = _MAINTENANCE_FEE.search(text)
            item = Item.maintenance
            point = "22"
            caveat = (
                "Это ставка домов под управлением мэрии, то есть минимум: в домах с другим "
                "управляющим, а это большинство новостроек, обычно платят больше."
            )
        else:
            raise ValueError("yerevan_arlis requires settings.item waste or maintenance")
        if found is None:
            raise ValueError(f"arlis act has no residential fee in point {point}")
        value = float(found.group(1).replace(",", "."))
        record = _record(
            ctx,
            source,
            item=item,
            value=value,
            unit="AMD/m2/month",
            valid_from=date(year, 1, 1),
            valid_to=date(year, 12, 31),
            url=url,
            note=(
                f"{_number(value)} AMD за м² жилья в месяц — решение Совета старейшин Еревана "
                f"о местных платежах на {year} год, пункт {point}. {caveat}"
            ),
        )
        return AdapterOutput(records=[record])


class YerevanWaterRecorded:
    """Official metered rates copied by hand from the scan of the yearly decision."""

    name = "yerevan_water_recorded"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        component = source.settings.get("component")
        recorded = _RECORDED_WATER.get(component) if isinstance(component, str) else None
        if recorded is None:
            raise ValueError("yerevan_water_recorded requires settings.component water or sewage")
        if ctx.period.year != _RECORDED_WATER_YEAR:
            raise ValueError(
                f"the water decision is copied for {_RECORDED_WATER_YEAR} only, not {ctx.period}"
            )
        item, value, service = recorded
        record = _record(
            ctx,
            source,
            item=item,
            value=value,
            unit="AMD/m3",
            valid_from=date(_RECORDED_WATER_YEAR, 1, 1),
            url=_require_url(source),
            note=(
                f"{_number(value)} AMD за м³ с НДС — {service}, обычные абоненты. Цифра переписана "
                "вручную со скана решения КРОУ 397-Н от 26.11.2025."
            ),
        )
        return AdapterOutput(records=[record])


register_adapter(YerevanPsrcGas())
register_adapter(YerevanPsrcElectricity())
register_adapter(YerevanPsrcScan())
register_adapter(YerevanArlis())
register_adapter(YerevanWaterRecorded())
