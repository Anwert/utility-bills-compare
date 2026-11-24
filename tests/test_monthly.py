"""The monthly run: saved sources in, published CityMonth files out.

Every test goes through ``run_month`` on saved pages. Expected euro amounts are
hand-worked from the saved tariffs, not recomputed by the code.
"""

from __future__ import annotations

import json
import lzma
import tarfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ubc.alerts import IssueSnapshot
from ubc.fetch import FetchError, FixtureFetcher
from ubc.houses import PinResult
from ubc.load import ConfigError, load_city, load_reference
from ubc.monthly import (
    MonthOptions,
    MonthReport,
    PublicationError,
    enrich_and_pin,
    run_month,
)
from ubc.paths import REPO_ROOT
from ubc.schemas import (
    CityConfig,
    CityId,
    HeatingType,
    HouseClass,
    Item,
    Mark,
    PinnedHouse,
    ReferenceData,
    YearMonth,
)
from ubc.secrets import SecretConfig
from ubc.site_index import build_site_index

FIXTURES = REPO_ROOT / "tests" / "fixtures"
OCTOBER = YearMonth.parse("2026-10")
NOW = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)


class NextMonth:
    """Answers another month's weather and rate with the saved October ones."""

    _DATES = (
        ("2027-01-01", "2026-10-01"),
        ("2027-01-31", "2026-10-31"),
        ("2026-02-01", "2025-11-01"),
    )

    def __init__(self, pages: SavedPages) -> None:
        self._pages = pages

    def _october(self, url: str) -> str:
        for other, october in self._DATES:
            url = url.replace(other, october)
        return url

    def fetch_bytes(self, url: str) -> bytes:
        return self._pages.fetch_bytes(url)

    def fetch_text(self, url: str) -> str:
        text = self._pages.fetch_text(self._october(url))
        if "open-meteo" in url:
            for other, october in self._DATES:
                text = text.replace(october, other)
            text = text.replace('"2026-10-', '"2027-01-')
        return text

    def post_text(self, url: str, body: str, *, headers: dict[str, str] | None = None) -> str:
        return self._pages.post_text(self._october(url), body, headers=headers)

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        return self._pages.render_html(url, budget_ms=budget_ms)

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        return self._pages.pdf_to_text(url, layout=layout)


class SavedPages:
    """Several fixture directories behind one fetcher. ``dead`` URLs raise FetchError."""

    def __init__(self, *roots: str | Path, dead: Sequence[str] = ()) -> None:
        self._roots = [
            FixtureFetcher(FIXTURES / root if isinstance(root, str) else root) for root in roots
        ]
        self._dead = set(dead)
        self.asked: list[str] = []

    def _root(self, url: str) -> FixtureFetcher:
        self.asked.append(url)
        if url in self._dead:
            raise FetchError(url, "saved as dead")
        for root in self._roots:
            if url in root.manifest:
                return root
        raise FetchError(url, "no fixture for this URL (network is not used)")

    def fetch_bytes(self, url: str) -> bytes:
        return self._root(url).fetch_bytes(url)

    def fetch_text(self, url: str) -> str:
        return self._root(url).fetch_text(url)

    def post_text(self, url: str, body: str, *, headers: dict[str, str] | None = None) -> str:
        return self._root(url).post_text(url, body, headers=headers)

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        return self._root(url).render_html(url, budget_ms=budget_ms)

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        return self._root(url).pdf_to_text(url, layout=layout)


def run(
    data_root: Path, cities: Sequence[CityConfig], fetcher: object, **overrides: object
) -> MonthReport:
    options: dict = {
        "period": OCTOBER,
        "cities": list(cities),
        "reference": load_reference(),
        "fetcher": fetcher,
        "data_root": data_root,
        "now": NOW,
    }
    options.update(overrides)
    return run_month(MonthOptions(**options))


def published(data_root: Path, name: str) -> dict:
    return json.loads((data_root / name).read_text(encoding="utf-8"))


def test_a_city_without_tariff_sources_gets_nine_empty_cells_and_the_euro_rate(
    tmp_path: Path,
) -> None:
    fetcher = SavedPages("open_meteo")
    run(tmp_path, [load_city("lisbon")], fetcher)

    assert not any("open-meteo" in url for url in fetcher.asked)
    month = published(tmp_path, "lisbon-2026-10.json")
    assert month["updated_at"] == "2026-11-07T06:00:00Z"
    assert month["exchange_rate"]["per_eur"] == 1
    assert len(month["cells"]) == 9
    assert {cell["status"] for cell in month["cells"]} == {"empty"}
    assert {cell["empty_reason"] for cell in month["cells"]} == {
        "источники тарифов для города ещё не подключены"
    }
    assert published(tmp_path, "site-index.json")["files"] == [
        {"path": "lisbon-2026-10.json", "example": False}
    ]


def test_a_city_without_tariff_sources_shows_the_real_rate_of_the_month(tmp_path: Path) -> None:
    run(tmp_path, [load_city("warsaw")], SavedPages("fx_ecb", "open_meteo"))

    rate = published(tmp_path, "warsaw-2026-10.json")["exchange_rate"]
    assert rate["currency"] == "PLN"
    assert rate["month"] == "2026-10"
    assert round(rate["per_eur"], 4) == 4.3813


# Yerevan on the saved pages


def norm(item: str, annual: float, unit: str, basis: str, heating: str | None = None) -> dict:
    return {
        "city": "yerevan",
        "house_class": "soviet_panel",
        "heating": heating,
        "item": item,
        "annual_value": annual,
        "unit": unit,
        "basis": basis,
        "source": "норма из теста",
    }


# 2 people: 100 kWh and 2 m3 a month. Gas boiler: 300 m3 a year.
REFERENCE = ReferenceData.model_validate(
    {
        "schema_version": 1,
        "apartment": {"rooms": 2, "area_m2": 60, "occupants": 2},
        "norms": [
            norm("electricity", 600, "kWh/person/year", "per_person"),
            norm("water", 12, "m3/person/year", "per_person"),
            norm("gas", 300, "m3/dwelling/year", "per_dwelling", heating="gas_boiler"),
        ],
    }
)
ANY_HOUSE_REFERENCE = ReferenceData.model_validate(
    REFERENCE.model_dump(mode="json")
    | {
        "norms": [
            item.model_dump(mode="json") | {"house_class": row.value}
            for item in REFERENCE.norms
            for row in HouseClass
        ]
    }
)
BOILER = (HouseClass.soviet_panel, HeatingType.gas_boiler)
DISTRICT = (HouseClass.soviet_panel, HeatingType.district)
NO_HOUSE = "дом из теста не найден"


def house(key: tuple[HouseClass, HeatingType] = BOILER) -> PinnedHouse:
    return PinnedHouse(
        id=f"house-{key[1].value}",
        city=CityId.yerevan,
        house_class=key[0],
        heating=key[1],
        listing_url="https://example.test/listing",
        address="Улица 1",
        first_seen="2026-10-01",
        last_seen="2026-10-31",
    )


class Houses:
    """Stands in for the classifier of #36 and remembers what it was given."""

    def __init__(self, *houses: PinnedHouse) -> None:
        self.pinned = {(item.house_class, item.heating): item for item in houses}
        self.calls: list[dict] = []

    def __call__(self, **given: object) -> PinResult:
        self.calls.append(given)
        return PinResult(
            houses=self.pinned,
            empty_reasons={
                (row, column): NO_HOUSE
                for row in HouseClass
                for column in HeatingType
                if (row, column) not in self.pinned
            },
        )


class Issues:
    """The GitHub slice ``sync_alerts`` uses, in memory."""

    def __init__(self) -> None:
        self.titles: list[str] = []
        self.bodies: list[str] = []

    def ensure_label(self, name: str, color: str, description: str) -> None: ...

    def list_open_issues(self, label: str) -> list[IssueSnapshot]:
        return []

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> IssueSnapshot:
        self.titles.append(title)
        self.bodies.append(body)
        return IssueSnapshot(len(self.titles), title, body, "open", tuple(labels))

    def comment(self, number: int, body: str) -> None: ...

    def close_issue(self, number: int) -> None: ...


YEREVAN_PAGES = ("yerevan", "fx_cba", "open_meteo", "myrealty", "real_estate_am")
ARLIS = "https://www.arlis.am/hy/acts/219208"
GAS_PAGE = "https://www.psrc.am/contents/fields/gas/gas_tariffs"


def run_yerevan(data_root: Path, *, dead: Sequence[str] = (), **overrides: object) -> MonthReport:
    options: dict = {"reference": REFERENCE, "house_step": Houses(house())}
    options.update(overrides)
    fetcher = options.pop("fetcher", None) or SavedPages(*YEREVAN_PAGES, dead=dead)
    return run(data_root, [load_city("yerevan")], fetcher, **options)


def filled(data_root: Path, month: str = "2026-10") -> dict:
    cells = published(data_root, f"yerevan-{month}.json")["cells"]
    (cell,) = [cell for cell in cells if cell["status"] == "ok"]
    return cell


def lines(cell: dict) -> dict[str, dict]:
    return {line["item"]: line for line in cell["lines"]}


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_yerevan_is_billed_by_the_day_price(tmp_path: Path) -> None:
    run_yerevan(tmp_path)

    cell = filled(tmp_path)
    amounts = {item: line["amount_eur"] for item, line in lines(cell).items()}
    # 405.045 AMD/EUR. Electricity: 100 kWh x 46.48 (day), not 36.48 (night).
    # Gas: 300 m3 x 54.9/2556.6 = 6.4421 m3 x 143.7.
    assert amounts == {
        "maintenance": 3.70,
        "electricity": 11.48,
        "gas": 2.29,
        "water": 0.84,
        "sewage": 0.15,
        "waste": 1.93,
    }
    assert cell["total_eur"] == 20.39


def test_empty_cells_carry_the_classifier_reason(tmp_path: Path) -> None:
    run_yerevan(tmp_path)

    cells = published(tmp_path, "yerevan-2026-10.json")["cells"]
    assert [cell["empty_reason"] for cell in cells if cell["status"] == "empty"] == [NO_HOUSE] * 8


def test_classifier_receives_the_listed_candidates_and_last_months_houses(tmp_path: Path) -> None:
    first = Houses(house())
    run_yerevan(tmp_path, house_step=first)
    second = Houses(house())
    run_yerevan(tmp_path, house_step=second)

    (call,) = first.calls
    assert call["city"].id is CityId.yerevan
    assert call["period"] == OCTOBER
    assert call["previous"] == []
    assert {candidate.id for candidate in call["candidates"]} >= {
        "myrealty-162352",
        "real-estate-427597",
    }
    assert [item.id for item in second.calls[0]["previous"]] == ["house-gas_boiler"]


def test_a_house_whose_fuel_tariff_is_not_connected_leaves_the_cell_empty(tmp_path: Path) -> None:
    run_yerevan(tmp_path, house_step=Houses(house(), house(DISTRICT)))

    cells = published(tmp_path, "yerevan-2026-10.json")["cells"]
    (district,) = [
        cell
        for cell in cells
        if (cell["house_class"], cell["heating"]) == ("soviet_panel", "district")
    ]
    assert district["status"] == "empty"
    assert "ещё не подключён" in district["empty_reason"]


def test_run_keeps_raw_answers_records_state_and_houses(tmp_path: Path) -> None:
    report = run_yerevan(tmp_path)

    assert set(report.written) == set(tree(tmp_path))
    normalized = published(tmp_path, "normalized/2026-10/yerevan.json")
    electricity = [
        entry for entry in normalized["sources"] if entry["source_id"] == "yerevan_electricity_psrc"
    ]
    assert [len(entry["records"]) for entry in electricity] == [2]
    assert published(tmp_path, "houses/yerevan.json")["houses"][0]["id"] == "house-gas_boiler"
    state = published(tmp_path, "state/yerevan.json")
    assert {article["item"] for article in state["articles"]} == {
        "maintenance",
        "electricity",
        "gas",
        "water",
        "sewage",
        "waste",
    }
    with tarfile.open(tmp_path / "raw/2026-10/yerevan/yerevan_waste_arlis.tar.xz") as packed:
        index = json.load(packed.extractfile("index.json"))
        assert index == [{"method": "fetch_text", "url": ARLIS, "file": "001.html"}]
        page = packed.extractfile("001.html").read()
    assert page.decode("utf-8") == (FIXTURES / "yerevan/arlis_219208.html").read_text("utf-8")


def test_unchanged_answers_give_identical_raw_files(tmp_path: Path) -> None:
    run_yerevan(tmp_path / "one")
    run_yerevan(tmp_path / "two")

    one, two = (
        {name: body for name, body in tree(tmp_path / part).items() if name.startswith("raw/")}
        for part in ("one", "two")
    )
    assert one and one == two


def test_dead_source_keeps_the_previous_value_as_stale_and_reports_it(tmp_path: Path) -> None:
    run_yerevan(tmp_path)
    issues = Issues()
    run_yerevan(tmp_path, dead=[GAS_PAGE], issues=issues)

    gas = lines(filled(tmp_path))["gas"]
    assert gas["amount_eur"] == 2.29
    assert "stale" in gas["marks"]
    assert "показано прошлое значение" in gas["note"]
    assert issues.titles == ["data-alert: Ереван — Газ — yerevan_gas_psrc"]


def test_dead_source_without_a_previous_value_publishes_nothing_but_still_alerts(
    tmp_path: Path,
) -> None:
    issues = Issues()

    with pytest.raises(PublicationError, match="electricity"):
        run_yerevan(
            tmp_path,
            dead=["https://www.psrc.am/contents/fields/electric_energy/el_energy_tariffs"],
            issues=issues,
        )

    assert tree(tmp_path) == {}
    assert len(issues.titles) == 1
    assert "yerevan_electricity_psrc" in issues.titles[0]


def test_one_broken_city_keeps_every_published_file_as_it_was(tmp_path: Path) -> None:
    run_yerevan(tmp_path)
    before = tree(tmp_path)

    with pytest.raises(PublicationError, match="нормы расхода"):
        run(
            tmp_path,
            [load_city("lisbon"), load_city("yerevan")],
            SavedPages(*YEREVAN_PAGES),
            reference=REFERENCE.model_copy(update={"norms": []}),
            house_step=Houses(house()),
        )

    assert tree(tmp_path) == before


@pytest.mark.parametrize(("missing", "problem"), [("fx_cba", "курса"), ("open_meteo", "градусо")])
def test_a_filled_cell_needs_the_rate_and_degree_days(
    tmp_path: Path, missing: str, problem: str
) -> None:
    pages = [name for name in YEREVAN_PAGES if name != missing]

    with pytest.raises(PublicationError, match=problem):
        run_yerevan(tmp_path, fetcher=SavedPages(*pages))

    assert tree(tmp_path) == {}


def test_city_without_a_rate_and_without_filled_cells_is_left_out(tmp_path: Path) -> None:
    issues = Issues()
    report = run(
        tmp_path,
        [load_city("warsaw"), load_city("lisbon")],
        SavedPages("open_meteo"),
        issues=issues,
    )

    assert [month.city for month in report.months] == [CityId.lisbon]
    assert not (tmp_path / "warsaw-2026-10.json").exists()
    assert any("нет курса" in warning for warning in report.warnings)
    assert issues.titles == ["data-alert: Варшава — Курс — fx_pln_ecb"]


def test_todays_rate_does_not_replace_the_monthly_average(tmp_path: Path) -> None:
    # Eurostat is missing; the NBS page is a list dated today, not an October average.
    report = run(tmp_path, [load_city("belgrade")], SavedPages("open_meteo", "fx_nbs"))

    assert report.months == ()


def test_dry_run_writes_nothing_and_sends_nothing(tmp_path: Path) -> None:
    issues = Issues()
    report = run_yerevan(tmp_path, dead=[ARLIS], issues=issues, dry_run=True, house_step=Houses())

    assert tree(tmp_path) == {}
    assert issues.titles == []
    assert report.alerts
    assert [month.city for month in report.months] == [CityId.yerevan]


def test_dead_listing_sites_are_a_warning_and_the_reason_of_the_empty_cells(
    tmp_path: Path,
) -> None:
    pages = SavedPages("yerevan", "fx_cba", "open_meteo")
    report = run_yerevan(tmp_path, fetcher=pages, house_step=enrich_and_pin)

    cells = published(tmp_path, "yerevan-2026-10.json")["cells"]
    assert {cell["empty_reason"] for cell in cells if cell["heating"] == "gas_boiler"} == {
        "сайты объявлений в этом месяце не дали ни одного дома"
    }
    assert any("myrealty" in warning for warning in report.warnings)


OVERPASS = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"


class BrokenOpenData(SavedPages):
    """A listing without storeys sends the run to Overpass, which breaks in a new way."""

    def fetch_text(self, url: str) -> str:
        return super().fetch_text(url).replace("Floor/Storeys", "Something else")

    def post_text(self, url: str, body: str, *, headers: dict[str, str] | None = None) -> str:
        if url == OVERPASS:
            self.asked.append(url)
            raise RuntimeError("the mirror answered with something unheard of")
        return super().post_text(url, body, headers=headers)


def test_real_classifier_runs_by_default_when_open_data_is_dead(tmp_path: Path) -> None:
    pages = BrokenOpenData(*YEREVAN_PAGES)
    report = run(tmp_path, [load_city("yerevan")], pages, reference=ANY_HOUSE_REFERENCE)

    assert OVERPASS in pages.asked
    assert any("открытые данные" in warning for warning in report.warnings)
    cells = published(tmp_path, "yerevan-2026-10.json")["cells"]
    assert len(cells) == 9
    (reason,) = {cell["empty_reason"] for cell in cells if cell["heating"] == "district"}
    assert reason != "дом такого класса с таким отоплением автоматически не найден"


def test_slow_open_data_stops_being_asked_once_its_time_is_spent(tmp_path: Path) -> None:
    pages = BrokenOpenData(*YEREVAN_PAGES)
    run(
        tmp_path,
        [load_city("yerevan")],
        pages,
        reference=ANY_HOUSE_REFERENCE,
        open_data_budget_s=0,
    )

    assert OVERPASS not in pages.asked
    assert (tmp_path / "yerevan-2026-10.json").is_file()


def test_one_article_run_takes_the_rest_from_the_saved_state(tmp_path: Path) -> None:
    run_yerevan(tmp_path)
    pages = SavedPages(*YEREVAN_PAGES)
    run_yerevan(tmp_path, fetcher=pages, item=Item.gas, house_step=Houses())

    assert ARLIS not in pages.asked
    assert not any("myrealty" in url for url in pages.asked)
    cell = filled(tmp_path)
    assert cell["total_eur"] == 20.39
    assert not any("stale" in line["marks"] for line in cell["lines"])
    normalized = published(tmp_path, "normalized/2026-10/yerevan.json")
    assert {"yerevan_waste_arlis", "yerevan_gas_psrc"} <= {
        entry["source_id"] for entry in normalized["sources"]
    }


# Tariffs that change over time. One example_html source stands in for electricity.

POWER = "https://example.test/power.html"


def power_page(root: Path, value: float, valid_from: str) -> SavedPages:
    root.mkdir(parents=True, exist_ok=True)
    (root / "power.html").write_text(
        f'<p data-item="electricity" data-value="{value}" data-unit="EUR/kWh" '
        f'data-currency="EUR" data-valid-from="{valid_from}"></p>',
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(json.dumps({POWER: "power.html"}), encoding="utf-8")
    return SavedPages(root, "open_meteo")


def lisbon_on_power() -> CityConfig:
    city = load_city("lisbon").model_dump(mode="json")
    for article in city["articles"]:
        if article["item"] == "electricity":
            article["sources"] = [
                {
                    "id": "lisbon_power",
                    "adapter": "example_html",
                    "kind": "official",
                    "reliability": "official",
                    "settings": {"url": POWER},
                }
            ]
    # The saved weather is October; other months reuse no degree-days in these bills.
    return CityConfig.model_validate(city)


LISBON_REFERENCE = ReferenceData.model_validate(
    {
        "schema_version": 1,
        "apartment": {"rooms": 2, "area_m2": 60, "occupants": 2},
        "norms": [
            norm("electricity", 600, "kWh/person/year", "per_person") | {"city": "lisbon"},
            norm("heating", 0, "kWh/dwelling/year", "per_dwelling", heating="electric")
            | {"city": "lisbon"},
        ],
    }
)
ELECTRIC = (HouseClass.soviet_panel, HeatingType.electric)


def run_lisbon(
    data_root: Path, value: float, valid_from: str, month: str = "2026-10", **overrides: object
) -> dict[str, dict]:
    lisbon_house = house(ELECTRIC).model_copy(update={"city": CityId.lisbon})
    options: dict = {
        "period": YearMonth.parse(month),
        "reference": LISBON_REFERENCE,
        "house_step": Houses(lisbon_house),
    }
    options.update(overrides)
    pages = options.pop("fetcher", None) or power_page(data_root / "pages", value, valid_from)
    run(data_root, [lisbon_on_power()], pages, **options)
    cells = published(data_root, f"lisbon-{month}.json")["cells"]
    (cell,) = [cell for cell in cells if cell["status"] == "ok"]
    return lines(cell)


def test_first_known_price_that_starts_inside_the_month_covers_it_whole(tmp_path: Path) -> None:
    bill = run_lisbon(tmp_path, 0.20, "2026-10-06")

    assert bill["electricity"]["amount_eur"] == 20
    assert bill["electricity"]["valid_from"] == "2026-10-06"
    assert "более ранняя цена неизвестна" in bill["electricity"]["note"]


def test_first_known_price_that_starts_after_the_month_is_not_used(tmp_path: Path) -> None:
    with pytest.raises(PublicationError, match="electricity"):
        run_lisbon(tmp_path, 0.20, "2026-11-01")


def test_changed_tariff_is_split_by_days_and_the_old_one_is_kept(tmp_path: Path) -> None:
    run_lisbon(tmp_path, 0.20, "2026-01-01")
    bill = run_lisbon(tmp_path, 0.25, "2026-10-17")

    # 100 kWh: 16 days at 20 EUR and 15 days at 25 EUR out of 31.
    assert bill["electricity"]["amount_eur"] == 22.42
    (state,) = published(tmp_path, "state/lisbon.json")["articles"]
    assert [entry["records"][0]["valid_from"] for entry in state["history"]] == [
        "2026-01-01",
        "2026-10-17",
    ]


def test_tariff_that_starts_after_the_month_leaves_the_old_one_in_force(tmp_path: Path) -> None:
    run_lisbon(tmp_path, 0.20, "2026-01-01")
    bill = run_lisbon(tmp_path, 0.25, "2026-11-01")

    assert bill["electricity"]["amount_eur"] == 20
    assert bill["electricity"]["valid_from"] == "2026-01-01"


def test_same_answer_does_not_grow_the_history(tmp_path: Path) -> None:
    run_lisbon(tmp_path, 0.20, "2026-01-01")
    run_lisbon(tmp_path, 0.20, "2026-01-01", now=datetime(2026, 11, 8, tzinfo=UTC))

    (state,) = published(tmp_path, "state/lisbon.json")["articles"]
    assert len(state["history"]) == 1


def test_corrected_value_of_the_same_date_replaces_the_old_one(tmp_path: Path) -> None:
    run_lisbon(tmp_path, 0.20, "2026-01-01")
    bill = run_lisbon(tmp_path, 0.25, "2026-01-01")

    assert bill["electricity"]["amount_eur"] == 25


def test_newer_answer_wins_even_if_it_dates_itself_earlier(tmp_path: Path) -> None:
    run_lisbon(tmp_path, 0.20, "2026-09-01")
    bill = run_lisbon(tmp_path, 0.25, "2026-06-01")

    assert bill["electricity"]["amount_eur"] == 25


def test_unconfirmed_jump_keeps_the_old_value_and_is_reported(tmp_path: Path) -> None:
    run_lisbon(tmp_path, 0.20, "2026-01-01")
    issues = Issues()
    bill = run_lisbon(tmp_path, 0.40, "2026-01-01", issues=issues)

    assert bill["electricity"]["amount_eur"] == 20
    assert "stale" in bill["electricity"]["marks"]
    assert len(issues.titles) == 1
    assert "0.4 EUR/kWh" in issues.bodies[0]


def november_state(root: Path, valid_from: str) -> dict:
    """State as if November had already been collected."""

    run_lisbon(root, 0.20, "2026-01-01")
    state = published(root, "state/lisbon.json")
    (article,) = state["articles"]
    article["checked"] = article["acting"]["period"] = "2026-11"
    article["acting"]["record"]["valid_from"] = valid_from
    article["history"][0]["records"][0]["valid_from"] = valid_from
    (root / "state/lisbon.json").write_text(json.dumps(state), encoding="utf-8")
    return state


def test_recount_of_an_older_month_uses_that_months_tariff(tmp_path: Path) -> None:
    state = november_state(tmp_path, "2026-01-01")

    # Today's page has a new price and backdates it into the recounted month.
    bill = run_lisbon(tmp_path, 0.22, "2026-10-01")

    assert bill["electricity"]["amount_eur"] == 20
    assert bill["electricity"]["marks"] == ["norm_based"]
    assert published(tmp_path, "state/lisbon.json") == state


def test_recount_of_a_month_before_the_history_takes_todays_answer(tmp_path: Path) -> None:
    state = november_state(tmp_path, "2026-11-01")

    bill = run_lisbon(tmp_path, 0.22, "2026-01-01")

    assert bill["electricity"]["amount_eur"] == 22
    assert published(tmp_path, "state/lisbon.json") == state


def test_source_whose_tariff_has_expired_is_reported_and_the_value_is_stale(
    tmp_path: Path,
) -> None:
    run_yerevan(tmp_path)
    january = YearMonth.parse("2027-01")
    state = published(tmp_path, "state/yerevan.json")
    waste = [article for article in state["articles"] if article["item"] == "waste"][0]
    assert waste["history"][0]["records"][0]["valid_to"] == "2026-12-31"
    issues = Issues()
    report = run_yerevan(
        tmp_path,
        period=january,
        issues=issues,
        dry_run=True,
        fetcher=NextMonth(SavedPages(*YEREVAN_PAGES)),
    )

    (cell,) = [cell for cell in report.months[0].cells if cell.status.value == "ok"]
    (line,) = [line for line in cell.lines if line.item is Item.waste]
    assert line.amount_eur == 1.93
    assert Mark.stale in line.marks
    assert [
        alert.detail for alert in report.alerts if alert.source_id == "yerevan_waste_arlis"
    ] == ["тариф источника действует только по 2026-12-31"]


# Secrets


def test_secret_values_never_reach_the_stored_files_or_the_issue(tmp_path: Path) -> None:
    class Leaky(SavedPages):
        def fetch_text(self, url: str) -> str:
            if url == ARLIS:
                raise FetchError(url, "proxy said hunter2secret")
            if url == "https://myrealty.am/en/apartments-for-sale/yerevan":
                return super().fetch_text(url) + "<!-- hunter2secret -->"
            return super().fetch_text(url)

    issues = Issues()
    run_yerevan(
        tmp_path,
        fetcher=Leaky(*YEREVAN_PAGES),
        secrets=SecretConfig(proxy_password="hunter2secret"),
        issues=issues,
        house_step=Houses(),
    )

    for name, body in tree(tmp_path).items():
        if name.endswith(".tar.xz"):
            body = lzma.decompress(body)
        assert b"hunter2secret" not in body, name
    assert issues.bodies and not any("hunter2secret" in body for body in issues.bodies)


# The repository keeps the new directories away from the site and the city loader


def test_stored_directories_are_not_snapshots_and_not_cities(tmp_path: Path) -> None:
    run_yerevan(tmp_path)

    assert build_site_index(tmp_path)["files"] == [
        {"path": "yerevan-2026-10.json", "example": False}
    ]


def test_unknown_adapter_in_a_city_file_stops_the_run(tmp_path: Path) -> None:
    city = load_city("lisbon").model_dump(mode="json")
    city["hdd_sources"][0]["adapter"] = "no_such_adapter"

    with pytest.raises(ConfigError, match="no_such_adapter"):
        run(tmp_path, [CityConfig.model_validate(city)], SavedPages("open_meteo"))
