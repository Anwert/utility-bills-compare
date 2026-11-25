"""Seam (a): rules of the monthly bill that no other test would miss.

Expected euro amounts are hand-worked literals.
"""

from __future__ import annotations

import pytest

from ubc.calculate import CalculationError, calculate_cell
from ubc.schemas import (
    Cell,
    CityId,
    ConsumptionNorm,
    HeatingType,
    HouseClass,
    HousingFee,
    Item,
    NormalizedRecord,
    PinnedHouse,
    YearMonth,
)


def record(item: str, value: float, unit: str, **changes: object) -> NormalizedRecord:
    data: dict = {
        "city": "lisbon",
        "item": item,
        "category": {"hdd": "context", "hdd_annual": "context", "maintenance": "housing"}.get(
            item, "utility"
        ),
        "value": value,
        "unit": unit,
        "currency": None if item.startswith("hdd") else "EUR",
        "valid_from": "2026-01-01",
        "source": {
            "kind": "official",
            "url": f"https://example.test/{item}",
            "fetched_at": "2026-11-24T00:00:00+00:00",
        },
        "method": "regex",
        "reliability": "official",
    }
    return NormalizedRecord.model_validate(data | changes)


def norm(item: str, annual: float, unit: str, **changes: object) -> ConsumptionNorm:
    data: dict = {
        "city": "lisbon",
        "house_class": "business",
        "heating": None,
        "item": item,
        "annual_value": annual,
        "unit": unit,
        "basis": "per_dwelling",
        "source": "норма из теста",
    }
    return ConsumptionNorm.model_validate(data | changes)


# 1200 kWh a year is 100 kWh a month; heating is off unless a test sets it.
POWER = record("electricity", 0.2, "EUR/kWh")
NORMS = [
    norm("electricity", 1200, "kWh/dwelling/year"),
    norm("heating", 0, "kWh/dwelling/year", heating="electric"),
]
WEATHER = [record("hdd", 100, "K*day"), record("hdd_annual", 1000, "K*day")]


def bill(
    *records: NormalizedRecord,
    norms: list[ConsumptionNorm] | None = None,
    fees: list[HousingFee] | None = None,
    weather: list[NormalizedRecord] | None = None,
) -> Cell:
    return calculate_cell(
        period=YearMonth.parse("2026-01"),
        city=CityId.lisbon,
        house_class=HouseClass.business,
        heating=HeatingType.electric,
        house=PinnedHouse(
            id="house-1",
            city=CityId.lisbon,
            house_class=HouseClass.business,
            heating=HeatingType.electric,
            housing_fees=fees or [],
            listing_url="https://example.test/listing",
            first_seen="2026-01-01",
            last_seen="2026-01-31",
        ),
        records=[*records, *(WEATHER if weather is None else weather)],
        norms=norms or NORMS,
    )


def amount(cell: Cell, item: Item) -> float:
    (line,) = [line for line in cell.lines if line.item is item]
    return line.amount_eur


@pytest.mark.parametrize(
    ("unit", "expected"),
    [("EUR/year", 10), ("EUR/m2/year", 600), ("EUR/person/year", 20), ("EUR/month", 120)],
)
def test_a_yearly_charge_is_spread_over_twelve_months(unit: str, expected: float) -> None:
    assert amount(bill(POWER, record("waste", 120, unit)), Item.waste) == expected


@pytest.mark.parametrize(
    ("period", "per", "expected"),
    [
        ("year", "per_dwelling", 10),
        ("month", "per_dwelling", 120),
        ("year", "per_m2", 600),
    ],
)
def test_a_yearly_fee_of_the_listing_is_spread_over_twelve_months(
    period: str, per: str, expected: float
) -> None:
    fee = HousingFee(amount=120, currency="EUR", period=period, per=per)

    assert amount(bill(POWER, fees=[fee]), Item.maintenance) == expected


def test_every_step_is_as_wide_as_the_distance_between_the_bounds() -> None:
    tiers = [
        {"up_to": 30, "value": 0.1},
        {"up_to": 50, "value": 0.2},
        {"up_to": 80, "value": 0.3},
        {"up_to": None, "value": 1},
    ]

    # 100 kWh: 30 at 0.1, 20 at 0.2, 30 at 0.3, 20 at 1 = 3 + 4 + 9 + 20.
    cell = bill(record("electricity", 0.1, "EUR/kWh", tiers=tiers))

    assert amount(cell, Item.electricity) == 36


def test_a_price_that_ended_leaves_the_rest_of_the_month_without_a_tariff() -> None:
    with pytest.raises(CalculationError, match="нет тарифа"):
        bill(record("electricity", 0.2, "EUR/kWh", valid_to="2026-01-20"))


def test_a_price_that_ended_gives_way_to_an_older_one_still_in_force() -> None:
    # 100 kWh: 10 days at 0.31 and 21 days at 0.62 out of 31 = 10 + 42.
    cell = bill(
        record("electricity", 0.62, "EUR/kWh", valid_from="2025-01-01"),
        record("electricity", 0.31, "EUR/kWh", valid_to="2026-01-10"),
    )

    assert amount(cell, Item.electricity) == 52


HEATED = [NORMS[0], norm("heating", 1000, "kWh/dwelling/year", heating="electric")]


def test_degree_days_of_another_month_are_not_used() -> None:
    other = record("hdd", 300, "K*day", valid_from="2026-02-01")

    # A tenth of 1000 kWh at 0.2.
    cell = bill(POWER, norms=HEATED, weather=[*WEATHER, other])

    assert amount(cell, Item.heating) == 20
    with pytest.raises(CalculationError, match="градусо-суток на месяц"):
        bill(POWER, norms=HEATED, weather=[other, WEATHER[1]])


def test_a_heating_year_without_degree_days_is_not_divided_by() -> None:
    with pytest.raises(CalculationError, match="отопительный год"):
        bill(POWER, norms=HEATED, weather=[WEATHER[0], record("hdd_annual", 0, "K*day")])
