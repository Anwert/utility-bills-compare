"""Seam (a): one cell's monthly bill from normalized records.

Expected euro amounts are hand-worked literals. They are not produced by
re-implementing the calculator.
"""

from __future__ import annotations

import pytest

from ubc.calculate import CalculationError, calculate_cell
from ubc.load import load_reference
from ubc.schemas import (
    Basis,
    Category,
    CellStatus,
    CityId,
    ClassFee,
    ConsumptionNorm,
    HeatingType,
    HouseClass,
    HousingFee,
    Item,
    Mark,
    NormalizedRecord,
    PinnedHouse,
    ReferenceApartment,
    YearMonth,
)

EMPTY_REASON = "дом такого класса с таким отоплением автоматически не найден"


def record(**overrides: object) -> NormalizedRecord:
    data: dict = {
        "city": "berlin",
        "item": "heating",
        "category": "utility",
        "value": 1,
        "unit": "EUR/month",
        "currency": "EUR",
        "valid_from": "2026-01-01",
        "source": {
            "kind": "official",
            "url": "https://example.test/heating",
            "fetched_at": "2026-11-24T00:00:00+00:00",
            "name": "fixture",
        },
        "method": "regex",
        "reliability": "official",
    }
    data.update(overrides)
    return NormalizedRecord.model_validate(data)


def norm(**overrides: object) -> ConsumptionNorm:
    data: dict = {
        "city": "berlin",
        "house_class": "soviet_panel",
        "heating": None,
        "item": "heating",
        "annual_value": 100,
        "unit": "kWh/m2/year",
        "basis": "per_m2",
        "source": "fixture norm",
    }
    data.update(overrides)
    return ConsumptionNorm.model_validate(data)


def house(**overrides: object) -> PinnedHouse:
    data: dict = {
        "id": "house-1",
        "city": "berlin",
        "house_class": "soviet_panel",
        "heating": "district",
        "listing_url": "https://example.test/listing",
        "address": "Улица 1",
        "first_seen": "2026-01-01",
        "last_seen": "2026-01-31",
    }
    data.update(overrides)
    return PinnedHouse.model_validate(data)


def hdd(value: float, *, annual: bool = False, month: str = "2026-01") -> NormalizedRecord:
    return record(
        item="hdd_annual" if annual else "hdd",
        category="context",
        value=value,
        unit="K*day",
        currency=None,
        valid_from=month,
        source={
            "kind": "official",
            "url": "https://example.test/hdd",
            "fetched_at": "2026-11-24T00:00:00+00:00",
            "name": "hdd",
        },
    )


def fx(value: float, currency: str = "AMD", *, month: str = "2026-01-01") -> NormalizedRecord:
    return record(
        city="all",
        item="fx",
        category="context",
        value=value,
        unit=f"{currency}/EUR",
        currency=currency,
        valid_from=month,
        source={
            "kind": "official",
            "url": "https://example.test/fx",
            "fetched_at": "2026-11-24T00:00:00+00:00",
            "name": "fx",
        },
    )


def bill(**overrides: object):
    args = {
        "period": YearMonth.parse("2026-01"),
        "city": CityId.berlin,
        "house_class": HouseClass.soviet_panel,
        "heating": HeatingType.district,
        "house": house(),
        "records": [],
        "norms": [],
        "apartment": ReferenceApartment(),
    }
    args.update(overrides)
    return calculate_cell(**args)


def cents(amount: float) -> str:
    return f"{amount:.2f}"


def line(cell, item: Item):
    found = [row for row in cell.lines if row.item is item]
    assert len(found) == 1
    return found[0]


def test_missing_house_can_carry_its_own_reason() -> None:
    cell = bill(house=None, empty_reason="в городе нет тарифа центрального отопления")

    assert cell.status is CellStatus.empty
    assert cell.empty_reason == "в городе нет тарифа центрального отопления"


@pytest.mark.parametrize(
    ("listing_fee", "official_rate", "expected"),
    [
        # 900 PLN a month for the flat, 4 PLN for 1 EUR.
        (True, True, [(225.0, "https://example.test/listing")]),
        # 10 PLN/m2 for 60 m2.
        (False, True, [(150.0, "https://example.test/maintenance")]),
        (False, False, []),
    ],
)
def test_housing_fee_comes_from_the_listing_then_the_official_rate_then_is_absent(
    listing_fee: bool, official_rate: bool, expected: list
) -> None:
    fee = HousingFee(amount=900, currency="PLN", period="month", per="per_dwelling")
    records = [
        record(city="warsaw", currency="PLN", unit="PLN/month", value=400),
        fx(4, "PLN"),
    ]
    if official_rate:
        records.append(
            record(
                city="warsaw",
                item="maintenance",
                category="housing",
                value=10,
                unit="PLN/m2/month",
                currency="PLN",
                source={
                    "kind": "official",
                    "url": "https://example.test/maintenance",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "official",
                },
            )
        )

    cell = bill(
        city=CityId.warsaw,
        house=house(city="warsaw", housing_fees=[fee] if listing_fee else []),
        records=records,
    )

    lines = [line for line in cell.lines if line.category is Category.housing]
    assert [(line.amount_eur, line.source_url) for line in lines] == expected


@pytest.mark.parametrize(
    ("valid_from", "house_class", "listing_fee", "expected"),
    [
        # 8 PLN/m2 for 60 m2 at 4 PLN for 1 EUR, instead of the official 10 PLN/m2.
        ("2026-01-01", "soviet_panel", False, (120.0, "https://example.test/minutes")),
        ("2026-01-02", "soviet_panel", False, (150.0, "https://example.test/maintenance")),
        ("2026-01-01", "business", False, (150.0, "https://example.test/maintenance")),
        ("2026-01-01", "soviet_panel", True, (225.0, "https://example.test/listing")),
    ],
    ids=["class fee", "not in force yet", "other class", "listing fee wins"],
)
def test_fee_of_a_real_house_of_the_class_replaces_the_official_rate(
    valid_from: str, house_class: str, listing_fee: bool, expected: tuple
) -> None:
    fee = HousingFee(amount=900, currency="PLN", period="month", per="per_dwelling")
    cell = bill(
        city=CityId.warsaw,
        house=house(city="warsaw", housing_fees=[fee] if listing_fee else []),
        records=[
            record(city="warsaw", currency="PLN", unit="PLN/month", value=400),
            fx(4, "PLN"),
            record(
                city="warsaw",
                item="maintenance",
                category="housing",
                value=10,
                unit="PLN/m2/month",
                currency="PLN",
                source={
                    "kind": "official",
                    "url": "https://example.test/maintenance",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "official",
                },
            ),
        ],
        class_fees=[
            ClassFee(
                house_class=house_class,
                amount_per_m2=8,
                currency="PLN",
                building="Дом 1",
                source_url="https://example.test/minutes",
                valid_from=valid_from,
                note="сбор другого дома",
            )
        ],
    )

    (line,) = [line for line in cell.lines if line.category is Category.housing]
    assert (line.amount_eur, line.source_url) == expected
    if line.source_url.endswith("minutes"):
        assert line.marks == [Mark.fixed]
        assert line.note == "сбор другого дома"


def test_missing_house_is_an_empty_cell_even_when_tariffs_exist() -> None:
    cell = bill(
        house=None,
        records=[record(value=10, unit="EUR/month")],
    )

    assert cell.status is CellStatus.empty
    assert cell.empty_reason == EMPTY_REASON
    assert cell.total_eur is None
    assert cell.housing_eur is None
    assert cell.utility_eur is None
    assert cell.lines == []
    assert cell.building is None
    assert cell.house_class is HouseClass.soviet_panel
    assert cell.heating is HeatingType.district


def test_fixed_payment_ignores_degree_days() -> None:
    tariff = record(value=2, unit="EUR/m2/month", valid_from="2026-01-01")
    january = bill(records=[tariff])
    july = bill(
        period=YearMonth.parse("2026-07"),
        records=[tariff],
    )

    assert cents(line(january, Item.heating).amount_eur) == "120.00"
    assert cents(line(july, Item.heating).amount_eur) == "120.00"
    assert line(january, Item.heating).marks == [Mark.fixed]
    assert january.housing_eur == 0
    assert cents(january.utility_eur) == "120.00"
    assert cents(january.total_eur) == "120.00"
    assert january.status is CellStatus.ok
    assert january.building is not None
    assert january.building.id == "house-1"


def test_norm_heating_follows_degree_days_and_is_zero_in_summer() -> None:
    tariff = record(value=0.1, unit="EUR/kWh")
    heating_norm = norm(annual_value=100, unit="kWh/m2/year", basis="per_m2")
    january = bill(
        records=[
            tariff,
            hdd(300, month="2026-01"),
            hdd(1000, annual=True, month="2025-07-01"),
        ],
        norms=[heating_norm],
    )
    july = bill(
        period=YearMonth.parse("2026-07"),
        records=[
            tariff,
            hdd(0, month="2026-07"),
            hdd(1000, annual=True, month="2025-07-01"),
        ],
        norms=[heating_norm],
    )

    assert cents(line(january, Item.heating).amount_eur) == "180.00"
    assert line(january, Item.heating).marks == [Mark.norm_based]
    assert cents(line(july, Item.heating).amount_eur) == "0.00"
    assert july.lines
    assert cents(july.total_eur) == "0.00"


def test_tariff_change_inside_the_month_is_weighted_by_days() -> None:
    # January has 31 days. The January 1 tariff stays open; the January 16
    # tariff replaces it. 15 days at 10 EUR and 16 days at 40 EUR:
    # 10 * 15/31 + 40 * 16/31 = 25.48387... EUR, rounded half up to 25.48.
    cell = bill(
        records=[
            record(value=10, unit="EUR/month", valid_from="2026-01-01"),
            record(
                value=40,
                unit="EUR/month",
                valid_from="2026-01-16",
                source={
                    "kind": "official",
                    "url": "https://example.test/heating-new",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "new",
                },
            ),
        ]
    )

    assert cents(line(cell, Item.heating).amount_eur) == "25.48"
    assert line(cell, Item.heating).source_url == "https://example.test/heating-new"
    assert line(cell, Item.heating).valid_from.isoformat() == "2026-01-16"


def test_gas_boiler_prices_the_heating_norm_on_the_gas_tariff() -> None:
    # 100 kWh/m²/year × 60 m² × 500/1000 = 3 000 kWh × 0.02 EUR = 60.00 EUR.
    cell = bill(
        heating=HeatingType.gas_boiler,
        house=house(heating="gas_boiler"),
        records=[
            record(
                item="gas",
                value=0.02,
                unit="EUR/kWh",
                source={
                    "kind": "official",
                    "url": "https://example.test/gas",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "gas",
                },
            ),
            hdd(500),
            hdd(1000, annual=True),
        ],
        norms=[norm(annual_value=100, unit="kWh/m2/year", basis="per_m2")],
    )

    assert cents(line(cell, Item.gas).amount_eur) == "60.00"
    assert line(cell, Item.gas).marks == [Mark.norm_based]
    assert line(cell, Item.gas).source_url == "https://example.test/gas"
    assert all(row.item is not Item.heating for row in cell.lines)


def test_stepped_tariff_uses_the_whole_months_consumption() -> None:
    # 1 200 kWh/person/year * 2 people / 12 = 200 kWh.
    # First 101 kWh at 0.10 and the next 99 at 0.20 = 29.90 EUR.
    cell = bill(
        heating=HeatingType.gas_boiler,
        house=house(heating="gas_boiler"),
        records=[
            record(
                item="electricity",
                value=0.1,
                unit="EUR/kWh",
                source={
                    "kind": "official",
                    "url": "https://example.test/electricity",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "steps",
                },
                tiers=[
                    {"up_to": 101, "value": 0.1},
                    {"up_to": None, "value": 0.2},
                ],
            ),
            record(item="gas", value=1, unit="EUR/month"),
        ],
        norms=[
            norm(
                item="electricity",
                annual_value=1200,
                unit="kWh/person/year",
                basis="per_person",
            )
        ],
    )

    assert cents(line(cell, Item.electricity).amount_eur) == "29.90"
    assert Mark.norm_based in line(cell, Item.electricity).marks


def test_stepped_tariff_is_not_re_tiered_on_a_partial_month() -> None:
    # 100 kWh in the month. Tariff A (15 days): 50 kWh at 0.10, then 1.00.
    # Tariff B (16 days): 0.10 flat. Full-month bills are 55 EUR and 10 EUR.
    # 55 * 15/31 + 10 * 16/31 = 31.774... EUR, rounded half up to 31.77.
    cell = bill(
        records=[
            record(
                value=0.1,
                unit="EUR/kWh",
                valid_from="2026-01-01",
                valid_to="2026-01-15",
                tiers=[
                    {"up_to": 50, "value": 0.1},
                    {"up_to": None, "value": 1},
                ],
            ),
            record(
                value=0.1,
                unit="EUR/kWh",
                valid_from="2026-01-16",
                tiers=[{"up_to": None, "value": 0.1}],
                source={
                    "kind": "official",
                    "url": "https://example.test/heating-new",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "new",
                },
            ),
            hdd(1000, month="2026-01"),
            hdd(1000, annual=True),
        ],
        norms=[norm(annual_value=100, unit="kWh/dwelling/year", basis="per_dwelling")],
    )

    assert cents(line(cell, Item.heating).amount_eur) == "31.77"


def test_local_currency_is_divided_by_the_monthly_rate() -> None:
    # 1 000 AMD / 400 AMD per EUR = 2.50 EUR.
    cell = bill(
        city=CityId.yerevan,
        house=house(city="yerevan"),
        records=[
            record(city="yerevan", value=1000, unit="AMD/month", currency="AMD"),
            fx(400, "AMD"),
        ],
    )

    assert cents(line(cell, Item.heating).amount_eur) == "2.50"
    assert cents(cell.total_eur) == "2.50"


def test_tetri_prices_are_hundredths_of_lari_before_the_rate() -> None:
    # 500 tetri = 5 GEL. 5 / 2 GEL per EUR = 2.50 EUR.
    cell = bill(
        city=CityId.tbilisi,
        house=house(city="tbilisi"),
        records=[
            record(city="tbilisi", value=500, unit="tetri/month", currency="GEL"),
            fx(2, "GEL"),
        ],
    )

    assert cents(line(cell, Item.heating).amount_eur) == "2.50"


def test_source_marks_reach_the_breakdown() -> None:
    cell = bill(
        records=[
            record(item="heating", value=10, unit="EUR/month"),
            record(
                item="electricity",
                value=0.1,
                unit="EUR/kWh",
                reliability="statistics",
                source={
                    "kind": "statistics",
                    "url": "https://example.test/electricity",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "eurostat",
                },
            ),
            record(
                item="water",
                value=1,
                unit="EUR/m3",
                reliability="third_party",
                source={
                    "kind": "third_party",
                    "url": "https://example.test/water",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "aggregator",
                },
            ),
            record(
                item="waste",
                value=3,
                unit="EUR/month",
                method="llm_vision",
                source={
                    "kind": "official",
                    "url": "https://example.test/waste",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "scan",
                },
            ),
        ],
        norms=[
            norm(
                item="electricity",
                annual_value=600,
                unit="kWh/person/year",
                basis="per_person",
            ),
            norm(item="water", annual_value=12, unit="m3/person/year", basis="per_person"),
        ],
        extra_marks={Item.water: [Mark.stale]},
    )

    assert line(cell, Item.heating).marks == [Mark.fixed]
    assert line(cell, Item.electricity).marks == [Mark.norm_based, Mark.statistics_estimate]
    assert line(cell, Item.water).marks == [Mark.norm_based, Mark.third_party, Mark.stale]
    assert line(cell, Item.waste).marks == [Mark.fixed, Mark.llm_extracted]
    assert cents(line(cell, Item.electricity).amount_eur) == "10.00"
    assert cents(line(cell, Item.water).amount_eur) == "2.00"
    assert line(cell, Item.water).source_url == "https://example.test/water"
    assert line(cell, Item.electricity).category is Category.utility
    assert cents(cell.utility_eur) == "25.00"
    assert cents(cell.total_eur) == "25.00"
    assert cell.housing_eur == 0


def test_listing_fee_is_the_housing_line_and_beats_the_city_tariff() -> None:
    pinned = house(
        housing_fees=[
            HousingFee(amount=10, currency="EUR", period="month", per="per_m2"),
        ]
    )
    cell = bill(
        house=pinned,
        records=[
            record(item="heating", value=1, unit="EUR/month"),
            record(
                item="maintenance",
                category="housing",
                value=99,
                unit="EUR/month",
                source={
                    "kind": "official",
                    "url": "https://example.test/maintenance",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "city",
                },
            ),
        ],
    )

    assert cents(line(cell, Item.maintenance).amount_eur) == "600.00"
    assert line(cell, Item.maintenance).marks == [Mark.fixed, Mark.third_party]
    assert line(cell, Item.maintenance).source_url == "https://example.test/listing"
    assert line(cell, Item.maintenance).category is Category.housing
    assert cents(cell.housing_eur) == "600.00"
    assert cents(cell.utility_eur) == "1.00"
    assert cents(cell.total_eur) == "601.00"


def test_electric_heating_is_priced_on_the_electricity_tariff_and_split_out() -> None:
    # Base: 600 kWh/person/year * 2 / 12 = 100 kWh.
    # Heat: 10 kWh/m2/year * 60 m2 * 200/1000 = 120 kWh.
    # Together 220 kWh on tiers 101 at 0.10 then 0.20:
    # 101 * 0.10 + 119 * 0.20 = 33.90.
    # Base alone is 100 * 0.10 = 10.00, so heating is 23.90.
    cell = bill(
        heating=HeatingType.electric,
        house=house(heating="electric"),
        records=[
            record(
                item="electricity",
                value=0.1,
                unit="EUR/kWh",
                tiers=[
                    {"up_to": 101, "value": 0.1},
                    {"up_to": None, "value": 0.2},
                ],
                source={
                    "kind": "official",
                    "url": "https://example.test/electricity",
                    "fetched_at": "2026-11-24T00:00:00+00:00",
                    "name": "power",
                },
            ),
            hdd(200, month="2026-01"),
            hdd(1000, annual=True),
        ],
        norms=[
            norm(
                item="electricity",
                annual_value=600,
                unit="kWh/person/year",
                basis="per_person",
            ),
            norm(item="heating", annual_value=10, unit="kWh/m2/year", basis="per_m2"),
        ],
    )

    assert cents(line(cell, Item.electricity).amount_eur) == "10.00"
    assert cents(line(cell, Item.heating).amount_eur) == "23.90"
    assert line(cell, Item.heating).source_url == "https://example.test/electricity"
    assert cents(cell.total_eur) == "33.90"


def _gas(**overrides: object) -> NormalizedRecord:
    return record(
        **{
            "item": "gas",
            "value": 0.1,
            "unit": "EUR/kWh",
            "source": {
                "kind": "official",
                "url": "https://example.test/gas",
                "fetched_at": "2026-11-24T00:00:00+00:00",
                "name": "gas",
            },
        }
        | overrides
    )


def _boiler(*norms: ConsumptionNorm, tariff: NormalizedRecord | None = None, **overrides: object):
    return bill(
        heating=HeatingType.gas_boiler,
        house=house(heating="gas_boiler"),
        records=[tariff or _gas(), hdd(200, month="2026-01"), hdd(1000, annual=True)],
        norms=list(norms),
        **overrides,
    )


def _base_gas(**overrides: object) -> ConsumptionNorm:
    return norm(
        **{
            "item": "gas",
            "annual_value": 1200,
            "unit": "kWh/dwelling/year",
            "basis": "per_dwelling",
            "source": "плита и горячая вода",
        }
        | overrides
    )


HEATING_GAS = norm(
    item="gas",
    heating="gas_boiler",
    annual_value=10,
    unit="kWh/m2/year",
    basis="per_m2",
    source="котёл",
)


def test_gas_for_cooking_and_for_the_boiler_are_two_lines_of_one_stepped_tariff() -> None:
    # Base: 1 200 kWh / 12 = 100 kWh.
    # Boiler: 10 kWh/m2/year * 60 m2 * 200/1000 = 120 kWh.
    # 220 kWh on tiers 101 at 0.10 then 0.20: 101 * 0.10 + 119 * 0.20 = 33.90.
    # Base alone is 10.00, so heating is 23.90.
    cell = _boiler(
        _base_gas(),
        HEATING_GAS,
        tariff=_gas(tiers=[{"up_to": 101, "value": 0.1}, {"up_to": None, "value": 0.2}]),
        extra_marks={Item.gas: [Mark.stale]},
    )

    assert cents(line(cell, Item.gas).amount_eur) == "10.00"
    assert cents(line(cell, Item.heating).amount_eur) == "23.90"
    assert cents(cell.total_eur) == "33.90"
    assert "плита и горячая вода" in line(cell, Item.gas).note
    assert "котёл" in line(cell, Item.heating).note
    for item in (Item.gas, Item.heating):
        assert line(cell, item).source_url == "https://example.test/gas"
        assert line(cell, item).marks == [Mark.norm_based, Mark.stale]


def test_gas_norms_of_different_energy_units_are_added_up() -> None:
    # Base: 4.32 GJ / 12 = 0.36 GJ = 100 kWh, 10.00 EUR. Boiler: 120 kWh, 12.00 EUR.
    cell = _boiler(_base_gas(annual_value=4.32, unit="GJ/dwelling/year"), HEATING_GAS)

    assert cents(line(cell, Item.gas).amount_eur) == "10.00"
    assert cents(line(cell, Item.heating).amount_eur) == "12.00"


def test_summer_gas_of_a_boiler_cell_is_the_base_use() -> None:
    cell = bill(
        period=YearMonth.parse("2026-07"),
        heating=HeatingType.gas_boiler,
        house=house(heating="gas_boiler"),
        records=[_gas(), hdd(0, month="2026-07"), hdd(1000, annual=True, month="2026-07")],
        norms=[_base_gas(), HEATING_GAS],
    )

    assert cents(line(cell, Item.gas).amount_eur) == "10.00"
    assert cents(line(cell, Item.heating).amount_eur) == "0.00"


def test_boiler_without_a_base_norm_keeps_one_gas_line() -> None:
    cell = _boiler(HEATING_GAS)

    assert [row.item for row in cell.lines] == [Item.gas]
    assert cents(line(cell, Item.gas).amount_eur) == "12.00"


def test_base_gas_norm_alone_does_not_heat_the_flat() -> None:
    with pytest.raises(CalculationError, match="норм"):
        _boiler(_base_gas())


def test_electric_heating_needs_the_everyday_use_norm_too() -> None:
    with pytest.raises(CalculationError, match="норм"):
        bill(
            heating=HeatingType.electric,
            house=house(heating="electric"),
            records=[_gas(item="electricity"), hdd(200), hdd(1000, annual=True)],
            norms=[norm()],
        )


@pytest.mark.parametrize(
    ("applies_to", "expected"),
    [([], "10.00"), (["electric"], "10.00"), (["gas_boiler"], None)],
)
def test_base_gas_is_billed_in_the_columns_its_norm_names(
    applies_to: list[str], expected: str | None
) -> None:
    cell = bill(
        heating=HeatingType.electric,
        house=house(heating="electric"),
        records=[
            _gas(),
            _gas(item="electricity"),
            hdd(200, month="2026-01"),
            hdd(1000, annual=True),
        ],
        norms=[
            _base_gas(applies_to=applies_to),
            norm(
                item="electricity",
                annual_value=600,
                unit="kWh/person/year",
                basis="per_person",
            ),
            norm(),
        ],
    )

    assert [cents(row.amount_eur) for row in cell.lines if row.item is Item.gas] == (
        [expected] if expected else []
    )


def test_gigajoule_tariff_converts_a_kilowatt_hour_norm() -> None:
    # 100 kWh/m2 * 60 m2 * 360/1000 = 2 160 kWh = 7.776 GJ.
    # 7.776 * 10 EUR/GJ = 77.76 EUR.
    cell = bill(
        records=[
            record(value=10, unit="EUR/GJ"),
            hdd(360, month="2026-01"),
            hdd(1000, annual=True),
        ],
        norms=[norm(annual_value=100, unit="kWh/m2/year", basis="per_m2")],
    )

    assert cents(line(cell, Item.heating).amount_eur) == "77.76"


def test_published_berlin_panel_norm_prices_january() -> None:
    # IWU MFH_E heat demand in data is 127.1 kWh/m²/year.
    # 127.1 * 60 * 100/1000 * 0.10 = 76.26 EUR.
    reference = load_reference()
    cell = bill(
        records=[
            record(value=0.1, unit="EUR/kWh"),
            hdd(100, month="2026-01"),
            hdd(1000, annual=True),
        ],
        norms=reference.norms,
    )

    assert cents(line(cell, Item.heating).amount_eur) == "76.26"
    assert line(cell, Item.heating).marks == [Mark.norm_based]


def test_missing_rate_tariff_or_degree_days_is_an_error_not_a_zero() -> None:
    heating = record(city="yerevan", value=1000, unit="AMD/month", currency="AMD")
    with pytest.raises(CalculationError, match="курс"):
        bill(city=CityId.yerevan, house=house(city="yerevan"), records=[heating])

    with pytest.raises(CalculationError, match="тариф"):
        bill(records=[])

    consumption = record(value=0.1, unit="EUR/kWh")
    heating_norm = norm()
    with pytest.raises(CalculationError, match="градусо-сут"):
        bill(records=[consumption], norms=[heating_norm])

    with pytest.raises(CalculationError, match="отопительный год"):
        bill(records=[consumption, hdd(100)], norms=[heating_norm])

    with pytest.raises(CalculationError, match="тариф"):
        bill(
            records=[
                record(value=10, unit="EUR/month", valid_from="2026-02-01"),
            ]
        )


def test_house_for_another_cell_is_rejected() -> None:
    with pytest.raises(CalculationError):
        bill(house=house(heating="electric"))


def test_consumption_norms_live_in_data_with_sources() -> None:
    reference = load_reference()

    assert reference.norms
    assert all(row.source != "stub" for row in reference.norms)

    for city in CityId:
        for house_class in HouseClass:
            heating_rows = [
                row
                for row in reference.norms
                if row.city is city and row.house_class is house_class and row.item is Item.heating
            ]
            assert heating_rows
            electricity = [
                row
                for row in reference.norms
                if row.city is city
                and row.house_class is house_class
                and row.item is Item.electricity
            ]
            assert len(electricity) == 1
            assert electricity[0].basis is Basis.per_person
            assert electricity[0].annual_value == (781 if city is CityId.yerevan else 950)
            assert electricity[0].heating is None

    warsaw_panel = next(
        row
        for row in reference.norms
        if row.city is CityId.warsaw
        and row.house_class is HouseClass.soviet_panel
        and row.item is Item.heating
    )
    assert warsaw_panel.annual_value == 261
    assert warsaw_panel.basis is Basis.per_m2
    assert "TABULA" in warsaw_panel.source

    lisbon_electric = [
        row
        for row in reference.norms
        if row.city is CityId.lisbon
        and row.item is Item.heating
        and row.heating is HeatingType.electric
    ]
    assert len(lisbon_electric) == 3
    assert {row.annual_value for row in lisbon_electric} == {230}
    assert {row.basis for row in lisbon_electric} == {Basis.per_dwelling}
    assert all("ICESD" in row.source for row in lisbon_electric)

    belgrade_water = next(
        row
        for row in reference.norms
        if row.city is CityId.belgrade
        and row.house_class is HouseClass.soviet_panel
        and row.item is Item.water
    )
    assert belgrade_water.annual_value == 91.2
    assert belgrade_water.basis is Basis.per_person
    assert "Infostan" in belgrade_water.source
