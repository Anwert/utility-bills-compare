"""Reference apartment and consumption-norm stubs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ubc.calculate import calculate_cell
from ubc.load import load_reference
from ubc.schemas import (
    ConsumptionNorm,
    Item,
    NormalizedRecord,
    PinnedHouse,
    ReferenceApartment,
    YearMonth,
)


def test_reference_apartment_is_the_shared_dwelling() -> None:
    reference = load_reference()

    assert reference.apartment.rooms == 2
    assert reference.apartment.area_m2 == 60
    assert reference.apartment.occupants == 2
    assert reference.norms
    assert all(norm.source != "stub" for norm in reference.norms)


def test_apartment_shape_is_fixed() -> None:
    with pytest.raises(ValidationError):
        ReferenceApartment.model_validate({"rooms": 3, "area_m2": 60, "occupants": 2})


def test_negative_norm_is_rejected() -> None:
    sample = load_reference().norms[0].model_dump(mode="json")
    sample["annual_value"] = -5

    with pytest.raises(ValidationError):
        ConsumptionNorm.model_validate(sample)


def _yerevan_records() -> list[NormalizedRecord]:
    """PSRC prices of October 2026, 400 AMD per euro, a tenth of the heating year."""

    rows = [
        ("yerevan", "electricity", 46.48, "AMD/kWh", "AMD"),
        ("yerevan", "gas", 143.7, "AMD/m3", "AMD"),
        ("yerevan", "water", 170.36, "AMD/m3", "AMD"),
        ("yerevan", "sewage", 30.06, "AMD/m3", "AMD"),
        ("all", "fx", 400, "AMD/EUR", "AMD"),
        ("yerevan", "hdd", 250, "K*day", None),
        ("yerevan", "hdd_annual", 2500, "K*day", None),
    ]
    return [
        NormalizedRecord.model_validate(
            {
                "city": city,
                "item": item,
                "category": "context" if item in {"fx", "hdd", "hdd_annual"} else "utility",
                "value": value,
                "unit": unit,
                "currency": currency,
                "valid_from": "2026-10-01",
                "source": {
                    "kind": "official",
                    "url": "https://example.test/tariff",
                    "fetched_at": "2026-11-01T00:00:00Z",
                },
                "method": "api",
                "reliability": "official",
            }
        )
        for city, item, value, unit, currency in rows
    ]


def _yerevan_lines(house_class: str, heating: str) -> dict[Item, float]:
    cell = calculate_cell(
        period=YearMonth.parse("2026-10"),
        city="yerevan",
        house_class=house_class,
        heating=heating,
        house=PinnedHouse(
            id="house",
            city="yerevan",
            house_class=house_class,
            heating=heating,
            listing_url="https://example.test/house",
            first_seen="2026-10-01",
            last_seen="2026-10-01",
        ),
        records=_yerevan_records(),
        norms=load_reference().norms,
    )
    return {line.item: line.amount_eur for line in cell.lines}


@pytest.mark.parametrize(
    ("house_class", "gas_eur"),
    # 22.5, 12.1 and 10.1 m3/m2 x 60 m2 x 10% of the year x 143.7 AMD / 400.
    [("soviet_panel", 48.50), ("typical_newbuild", 26.08), ("business", 21.77)],
)
def test_yerevan_gas_boiler_cell_has_every_norm_it_needs(house_class: str, gas_eur: float) -> None:
    lines = _yerevan_lines(house_class, "gas_boiler")

    assert lines[Item.gas] == gas_eur
    # 37.2 m3 x 2 people / 12 months = 6.2 m3.
    assert lines[Item.water] == 2.64
    assert lines[Item.sewage] == 0.47
    assert Item.heating not in lines


@pytest.mark.parametrize("house_class", ["soviet_panel", "typical_newbuild", "business"])
def test_yerevan_electric_cell_has_every_norm_it_needs(house_class: str) -> None:
    lines = _yerevan_lines(house_class, "electric")

    # 1900 kWh / 12 x 46.48 AMD / 400.
    assert lines[Item.electricity] == 18.40
    assert lines[Item.heating] > 0
    assert Item.gas not in lines
