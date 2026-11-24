"""Yerevan tariff adapters on saved PSRC and arlis pages (seam b).

Expected figures are the 24 November 2026 matrix literals, not values
recomputed by the adapter.
"""

from __future__ import annotations

import pytest

from ubc.adapter import CollectContext, get_adapter
from ubc.calculate import calculate_cell
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import (
    ConsumptionNorm,
    Item,
    NormalizedRecord,
    PinnedHouse,
    Reliability,
    SourceConfig,
    SourceKind,
    YearMonth,
    ordered_enabled_sources,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "yerevan"

GAS_PAGE = "https://www.psrc.am/contents/fields/gas/gas_tariffs"
GAS_PDF = (
    "https://www.psrc.am/uploads/files/%D4%B2%D5%B6%D5%A1%D5%AF%D5%A1%D5%B6%20%D5%A3%D5%A1%D5%A6/"
    "%D5%8D%D5%A1%D5%AF%D5%A1%D5%A3%D5%B6%D5%A5%D6%80/"
    "%D4%B2%D5%B6%D5%A1%D5%AF%D5%A1%D5%B6%20%D5%A3%D5%A1%D5%A6%D5%AB%20%D5%BD%D5%A1%D5%AF%D5%A1%D5%A3"
    "%D5%B6%D5%A5%D6%80%D5%A8%20%D5%AF%D5%A1%D5%B5%D6%84%D5%AB%20%D5%B0%D5%A1%D5%B4%D5%A1%D6%80.pdf"
)
ELECTRICITY_PAGE = "https://www.psrc.am/contents/fields/electric_energy/el_energy_tariffs"
ELECTRICITY_PDF = (
    "https://www.psrc.am/uploads/files/%D4%B7%D5%AC%D5%A5%D5%AF%D5%BF%D6%80%D5%A1%D5%AF%D5%A1%D5%B6"
    "%20%D4%B7%D5%B6%D5%A5%D6%80%D5%A3%D5%AB%D5%A1/%D5%8D%D5%A1%D5%AF%D5%A1%D5%A3%D5%B6%D5%A5%D6%80/"
    "2025/Sakagner.pdf"
)
WATER_PAGE = "https://www.psrc.am/contents/fields/water/water_tariffs"
WATER_PDF = (
    "https://www.psrc.am/uploads/files/%D5%8B%D5%B8%D6%82%D6%80/2026/07-01-26/"
    "ed7237be2e8caac682d9207afc87ff938aecf2001ab7b3ef40906b217165a563.pdf"
)
ARLIS_ACT = "https://www.arlis.am/hy/acts/219208"


def _collect(
    adapter: str,
    source_id: str,
    *,
    kind: SourceKind,
    reliability: Reliability,
    settings: dict,
    period: str = "2026-10",
    fetcher: FixtureFetcher | None = None,
):
    return get_adapter(adapter).collect(
        CollectContext(
            city=load_city("yerevan"),
            period=YearMonth.parse(period),
            fetcher=fetcher or FixtureFetcher(FIXTURES),
            source=SourceConfig(
                id=source_id,
                adapter=adapter,
                kind=kind,
                reliability=reliability,
                settings=settings,
            ),
        )
    )


def test_psrc_gas_is_the_vat_price_per_cubic_metre() -> None:
    output = _collect(
        "yerevan_psrc_gas",
        "yerevan_gas_psrc",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": GAS_PAGE},
    )

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city.value == "yerevan"
    assert record.item is Item.gas
    assert record.category.value == "utility"
    assert record.value == 143.7
    assert record.unit == "AMD/m3"
    assert record.currency is not None and record.currency.value == "AMD"
    assert record.tiers == []
    # The PDF was created on 6 October; the price on it covers the whole month.
    assert record.valid_from.isoformat() == "2026-10-01"
    assert "2026-10-06" in record.note
    assert record.valid_to is None
    assert record.method.value == "regex"
    assert record.reliability is Reliability.official
    assert record.source.kind is SourceKind.official
    assert record.source.url == GAS_PDF
    assert record.source.fetched_at.tzinfo is not None
    assert "1.2" in record.note


def test_psrc_gas_keeps_the_pdf_date_when_the_pdf_is_older_than_the_month() -> None:
    (record,) = _collect(
        "yerevan_psrc_gas",
        "yerevan_gas_psrc",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": GAS_PAGE},
        period="2026-11",
    ).records

    assert record.valid_from.isoformat() == "2026-10-06"


class _GasText(FixtureFetcher):
    """The saved PSRC pages, with another text layer for the gas PDF."""

    def __init__(self, text: str) -> None:
        super().__init__(FIXTURES)
        self.text = text

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        del layout
        assert url == GAS_PDF
        return self.text


def test_psrc_gas_reads_the_same_price_when_xpdf_shifts_the_row_labels() -> None:
    (record,) = _collect(
        "yerevan_psrc_gas",
        "yerevan_gas_psrc",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": GAS_PAGE},
        fetcher=_GasText((FIXTURES / "gas_xpdf.txt").read_text(encoding="utf-8")),
    ).records

    assert record.value == 143.7


@pytest.mark.parametrize(
    "text",
    [
        "1.1 83,333.33 100,000.00\n1.2 194.92 233.90\n",
        "1.1 83,333.33 100,000.00\n1.2 119,750.00 150,000.00\n",
        "1.1 83,333.33 100,000.00\n",
        "1.1 83,333.33 100,000.00\n119,750.00 143,700.00\n",
    ],
    ids=["dollars for 1.2", "not 20% VAT", "one row", "no label 1.2"],
)
def test_psrc_gas_refuses_a_table_that_is_not_the_household_one(text: str) -> None:
    with pytest.raises(ValueError, match="1.2"):
        _collect(
            "yerevan_psrc_gas",
            "yerevan_gas_psrc",
            kind=SourceKind.official,
            reliability=Reliability.official,
            settings={"url": GAS_PAGE},
            fetcher=_GasText(text),
        )


def test_psrc_electricity_splits_day_and_night_household_steps() -> None:
    output = _collect(
        "yerevan_psrc_electricity",
        "yerevan_electricity_psrc",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": ELECTRICITY_PAGE},
    )

    assert output.candidates == []
    assert len(output.records) == 2
    day, night = output.records
    assert day.item is Item.electricity and night.item is Item.electricity
    assert day.category.value == "utility" and night.category.value == "utility"
    assert day.unit == "AMD/kWh" and night.unit == "AMD/kWh"
    assert day.currency is not None and day.currency.value == "AMD"
    assert night.currency is not None and night.currency.value == "AMD"
    assert day.value == 46.48
    assert night.value == 36.48
    assert [(tier.up_to, tier.value) for tier in day.tiers] == [
        (201, 46.48),
        (401, 48.48),
        (None, 53.48),
    ]
    assert [(tier.up_to, tier.value) for tier in night.tiers] == [
        (201, 36.48),
        (401, 38.48),
        (None, 43.48),
    ]
    assert day.valid_from.isoformat() == "2026-01-26"
    assert night.valid_from.isoformat() == "2026-01-26"
    assert day.method.value == "regex" and night.method.value == "regex"
    assert day.reliability is Reliability.official
    assert day.source.url == ELECTRICITY_PDF
    assert night.source.url == ELECTRICITY_PDF
    assert day.note.startswith("дневной")
    assert night.note.startswith("ночной")
    assert "29.99" in day.note


def test_a_cell_is_billed_at_the_day_price_not_the_night_one() -> None:
    records = _collect(
        "yerevan_psrc_electricity",
        "yerevan_electricity_psrc",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": ELECTRICITY_PAGE},
    ).records
    fx = NormalizedRecord.model_validate(
        {
            "city": "all",
            "item": "fx",
            "category": "context",
            "value": 400,
            "unit": "AMD/EUR",
            "currency": "AMD",
            "valid_from": "2026-10-01",
            "source": {
                "kind": "official",
                "url": "https://example.test/fx",
                "fetched_at": "2026-11-01T00:00:00Z",
            },
            "method": "api",
            "reliability": "official",
        }
    )
    hdd = [
        fx.model_copy(
            update={
                "city": "yerevan",
                "item": item,
                "value": value,
                "unit": "K*day",
                "currency": None,
            }
        )
        for item, value in ((Item.hdd, 0), (Item.hdd_annual, 2500))
    ]
    house = PinnedHouse(
        id="house",
        city="yerevan",
        house_class="business",
        heating="electric",
        listing_url="https://example.test/house",
        first_seen="2026-10-01",
        last_seen="2026-10-01",
    )
    norms = [
        ConsumptionNorm(
            city="yerevan",
            house_class="business",
            item=item,
            annual_value=1200,
            unit="kWh/dwelling/year",
            basis="per_dwelling",
            source="test",
        )
        for item in (Item.electricity, Item.heating)
    ]

    cell = calculate_cell(
        period=YearMonth.parse("2026-10"),
        city="yerevan",
        house_class="business",
        heating="electric",
        house=house,
        records=[*records, fx, *hdd],
        norms=norms,
    )

    electricity = [line for line in cell.lines if line.item is Item.electricity]
    # 100 kWh x 46.48 AMD / 400 AMD per euro.
    assert [line.amount_eur for line in electricity] == [11.62]


def test_arlis_waste_and_maintenance_fees_for_2026() -> None:
    waste = _collect(
        "yerevan_arlis",
        "yerevan_waste_arlis",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": ARLIS_ACT, "item": "waste"},
    ).records
    maintenance = _collect(
        "yerevan_arlis",
        "yerevan_maintenance_arlis",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": ARLIS_ACT, "item": "maintenance"},
        period="2027-03",
    ).records

    assert len(waste) == 1 and len(maintenance) == 1
    assert waste[0].item is Item.waste
    assert waste[0].category.value == "utility"
    assert waste[0].value == 13
    assert waste[0].unit == "AMD/m2/month"
    assert waste[0].currency is not None and waste[0].currency.value == "AMD"
    assert waste[0].valid_from.isoformat() == "2026-01-01"
    assert waste[0].valid_to is not None
    assert waste[0].valid_to.isoformat() == "2026-12-31"
    assert waste[0].source.url == ARLIS_ACT
    assert waste[0].method.value == "regex"
    assert waste[0].reliability is Reliability.official
    assert maintenance[0].item is Item.maintenance
    assert maintenance[0].category.value == "housing"
    assert maintenance[0].value == 25
    assert maintenance[0].unit == "AMD/m2/month"
    assert maintenance[0].valid_from.isoformat() == "2026-01-01"
    assert maintenance[0].valid_to is not None
    assert maintenance[0].valid_to.isoformat() == "2026-12-31"
    assert "2026" in maintenance[0].note


def test_water_scan_is_disabled_and_statistics_keeps_the_official_split() -> None:
    with pytest.raises(ValueError, match="llm_vision"):
        _collect(
            "yerevan_psrc_scan",
            "yerevan_water_psrc_scan",
            kind=SourceKind.official,
            reliability=Reliability.official,
            settings={"url": WATER_PAGE},
        )

    water = _collect(
        "yerevan_water_stat",
        "yerevan_water_last_official",
        kind=SourceKind.statistics,
        reliability=Reliability.statistics,
        settings={"url": WATER_PDF, "component": "water"},
    ).records[0]
    sewage = _collect(
        "yerevan_water_stat",
        "yerevan_sewage_last_official",
        kind=SourceKind.statistics,
        reliability=Reliability.statistics,
        settings={"url": WATER_PDF, "component": "sewage"},
    ).records[0]

    assert water.item is Item.water and sewage.item is Item.sewage
    assert water.value == 170.36
    assert sewage.value == 30.06
    assert water.unit == "AMD/m3" and sewage.unit == "AMD/m3"
    assert water.currency is not None and water.currency.value == "AMD"
    assert water.valid_from.isoformat() == "2026-01-01"
    assert sewage.valid_from.isoformat() == "2026-01-01"
    assert water.valid_to is None and sewage.valid_to is None
    assert water.reliability is Reliability.statistics
    assert sewage.reliability is Reliability.statistics
    assert water.source.kind is SourceKind.statistics
    assert water.source.url == WATER_PDF
    assert sewage.source.url == WATER_PDF
    assert water.method.value == "regex"
    assert "397" in water.note
    assert "200.42" in water.note
    assert "доплату" in water.note
    assert "доплату" in sewage.note


def test_yerevan_config_enables_the_fallbacks_and_leaves_the_scan_off() -> None:
    city = load_city("yerevan")
    by_item = {article.item: article.sources for article in city.articles}

    assert [source.adapter for source in by_item[Item.gas]] == ["yerevan_psrc_gas"]
    assert [source.adapter for source in by_item[Item.electricity]] == ["yerevan_psrc_electricity"]
    assert [source.adapter for source in by_item[Item.waste]] == ["yerevan_arlis"]
    assert [source.adapter for source in by_item[Item.maintenance]] == ["yerevan_arlis"]
    assert by_item[Item.heating] == []
    assert by_item[Item.hot_water] == []

    for item in (Item.water, Item.sewage):
        sources = by_item[item]
        assert [source.adapter for source in sources] == ["yerevan_psrc_scan", "yerevan_water_stat"]
        assert sources[0].enabled is False
        assert sources[0].kind is SourceKind.official
        assert sources[1].enabled is True
        assert sources[1].reliability is Reliability.statistics
        assert [source.adapter for source in ordered_enabled_sources(sources)] == [
            "yerevan_water_stat"
        ]
