"""Yerevan gas, electricity, waste, maintenance, and the water fallback.

PSRC tariff sections are empty without JavaScript, so collection starts from
``render_html`` (a saved DOM in tests). The consumer PDF is the link whose
text is the tariff table, not a yearly review slide. Armenian row labels are
not in the PDF text layer; gas and electricity are read by position.

The drinking-water decision is a scan. That source stays disabled and refuses
to invent a tariff (``llm_vision`` is off). Water and sewage then come from the
recorded official split in decision 397-N (1 January 2026). No water price
index is applied: Eurostat has no Armenia series, and the Armstat CPI page
checked on 24 November 2026 has no water-tariff row.
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
_GAS_LABEL = re.compile(r"(?m)^\s*1\.2\b")
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

# Decision 397-N, metered subscribers, with VAT. The PDF has no text layer.
_RECORDED_WATER: dict[str, tuple[Item, float]] = {
    "water": (Item.water, 170.36),
    "sewage": (Item.sewage, 30.06),
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


def _dated(created: date) -> str:
    """Note for a PDF that prints no effective date: its creation date stands in.

    The pipeline prorates by it, and stretches a first-known price to the 1st.
    """

    return f"Дата действия в тексте PDF не указана, PDF создан {created.isoformat()}."


def _household_gas(text: str) -> float:
    """AMD per 1000 m³ with VAT from row 1.2.

    Row labels and numbers sit on different baselines, so a label lands beside
    its own pair with poppler but beside another one with xpdf. The pairs keep
    their order in both: 1.1 first, 1.2 second.
    """

    pairs = [
        (float(net.replace(",", "")), float(gross.replace(",", "")))
        for net, gross in _GAS_PAIR.findall(text)
    ]
    if len(pairs) < 2 or _GAS_LABEL.search(text) is None:
        raise ValueError("gas PDF has no row 1.2 with two tariff columns")
    net, gross = pairs[1]
    if not 10_000 <= gross <= 1_000_000 or abs(net * 1.2 - gross) > 1:
        raise ValueError(f"gas PDF row 1.2 is not AMD per 1000 m³ with 20% VAT: {net}, {gross}")
    return gross


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


def _slashed(prices: list[float]) -> str:
    return " / ".join(f"{price:g}" for price in prices)


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
    """Household natural gas, row 1.2, column with VAT, AMD per m³."""

    name = "yerevan_psrc_gas"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        page = _require_url(source)
        pdf_url = _pick_pdf(ctx, page, _is_gas_tariff)
        per_thousand = _household_gas(ctx.fetcher.pdf_to_text(pdf_url))
        valid_from = _created_on(ctx.fetcher.fetch_bytes(pdf_url))
        value = per_thousand / 1000
        note = (
            f"строка 1.2, столбец с НДС: {per_thousand:.0f} AMD за 1000 м³ "
            f"({value} AMD/м³). Подписи строк в текстовом слое нет, разбор позиционный. "
            f"{_dated(valid_from)}"
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
        valid_from = _created_on(ctx.fetcher.fetch_bytes(pdf_url))
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
                "дневной бытовой тариф 0,38 кВ, AMD/кВт·ч при расходе до 200 / 201–400 / "
                f"больше 400 кВт·ч в месяц: {_slashed(day_prices)}. Строка 1 (29.99/19.99) — "
                "льготная для социально уязвимых, не этот тариф. Счёт считается по дневной "
                "цене на весь расход: для доли ночного расхода источника нет. Подписей в "
                f"текстовом слое нет. {_dated(valid_from)}"
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
                "ночной бытовой тариф 0,38 кВ, те же ступени, что у дневного: "
                f"{_slashed(night_prices)} AMD/кВт·ч."
            ),
        )
        return AdapterOutput(records=[day, night])


class YerevanPsrcScan:
    """Official water decision. Disabled: the PDF is a scan and vision is off."""

    name = "yerevan_psrc_scan"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        page = _require_url(source)
        pdf_url = _pick_pdf(ctx, page, _is_drinking_water)
        ctx.fetcher.pdf_to_text(pdf_url)
        raise ValueError("PSRC water decision is a scan; llm_vision is disabled")


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
        elif item_name == "maintenance":
            found = _MAINTENANCE_FEE.search(text)
            item = Item.maintenance
            point = "22"
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
                f"п.{point} решения о местных платежах {year} года, жилое помещение, "
                f"{value:.0f} AMD/м² в месяц. Следующий год — новый акт; "
                "поиск arlis /hy/search/page/ с этой машины отвечает 403, "
                "поэтому URL акта задан в конфигурации города."
            ),
        )
        return AdapterOutput(records=[record])


class YerevanWaterStat:
    """Last official metered rates, used because the decision PDF cannot be read."""

    name = "yerevan_water_stat"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        component = source.settings.get("component")
        recorded = _RECORDED_WATER.get(component) if isinstance(component, str) else None
        if recorded is None:
            raise ValueError("yerevan_water_stat requires settings.component water or sewage")
        item, value = recorded
        note = (
            "записанное официальное значение решения PSRC №397-Н с 01.01.2026: "
            "питьевая вода 170.36 AMD/м³ и водоотведение 30.06 AMD/м³ с НДС "
            "(вместе 200.42). Скан решения без текстового слоя, llm_vision выключен. "
            "Индекс цен на воду не применён: Eurostat не включает Армению, "
            "на странице ИПЦ Armstat отдельного ряда на воду нет. " + ctx.city.paid_data_note
        )
        record = _record(
            ctx,
            source,
            item=item,
            value=value,
            unit="AMD/m3",
            valid_from=date(2026, 1, 1),
            url=_require_url(source),
            note=note,
        )
        return AdapterOutput(records=[record])


register_adapter(YerevanPsrcGas())
register_adapter(YerevanPsrcElectricity())
register_adapter(YerevanPsrcScan())
register_adapter(YerevanArlis())
register_adapter(YerevanWaterStat())
