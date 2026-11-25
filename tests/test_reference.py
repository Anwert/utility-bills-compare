"""Reference apartment and consumption-norm stubs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ubc.calculate import calculate_cell
from ubc.load import load_reference
from ubc.schemas import (
    Cell,
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


def _yerevan_cell(house_class: str, heating: str) -> Cell:
    return calculate_cell(
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


def _yerevan_lines(house_class: str, heating: str) -> dict[Item, float]:
    return {line.item: line.amount_eur for line in _yerevan_cell(house_class, heating).lines}


@pytest.mark.parametrize(
    ("house_class", "heating_eur"),
    # 8.2, 4.4 and 3.7 m3/m2 x 60 m2 x 10% of the year x 143.7 AMD / 400.
    [("soviet_panel", 17.68), ("typical_newbuild", 9.48), ("business", 7.98)],
)
def test_yerevan_gas_boiler_cell_has_every_norm_it_needs(
    house_class: str, heating_eur: float
) -> None:
    lines = _yerevan_lines(house_class, "gas_boiler")

    assert lines[Item.heating] == heating_eur
    # 131 m3 x 2 people / 12 months x 143.7 AMD / 400.
    assert lines[Item.gas] == 7.84
    # 37.2 m3 x 2 people / 12 months = 6.2 m3.
    assert lines[Item.water] == 2.64
    assert lines[Item.sewage] == 0.47


@pytest.mark.parametrize("house_class", ["soviet_panel", "typical_newbuild", "business"])
def test_yerevan_electric_cell_has_every_norm_it_needs(house_class: str) -> None:
    lines = _yerevan_lines(house_class, "electric")

    # 781 kWh x 2 people / 12 x 46.48 AMD / 400.
    assert lines[Item.electricity] == 15.13
    assert lines[Item.heating] > 0
    # Nothing says that an electrically heated flat cooks on gas.
    assert Item.gas not in lines


@pytest.mark.parametrize("heating", ["gas_boiler", "electric"])
@pytest.mark.parametrize(
    ("house_class", "caveat"),
    [
        ("soviet_panel", "Разбивки по типам домов нет"),
        ("typical_newbuild", "допущение"),
        ("business", "допущение"),
    ],
)
def test_yerevan_heating_line_says_how_far_its_norm_can_be_trusted(
    house_class: str, caveat: str, heating: str
) -> None:
    (line,) = [
        line for line in _yerevan_cell(house_class, heating).lines if line.item is Item.heating
    ]

    assert caveat in line.note


def test_published_norm_sources_are_written_for_a_reader() -> None:
    for norm in load_reference().norms:
        assert "#" not in norm.source
        assert "403" not in norm.source


def test_lisbon_boiler_flat_pays_for_cooking_and_for_heating() -> None:
    # 1610 kWh / 12 and 1150 kWh x 10% of the year, 0.10 EUR per kWh.
    gas = {"value": 0.1, "unit": "EUR/kWh", "currency": "EUR"}
    records = [
        NormalizedRecord.model_validate(
            record.model_dump(mode="json")
            | {"city": "lisbon"}
            | (gas if record.item is Item.gas else {})
        )
        for record in _yerevan_records()
        if record.item in {Item.gas, Item.hdd, Item.hdd_annual}
    ]
    cell = calculate_cell(
        period=YearMonth.parse("2026-10"),
        city="lisbon",
        house_class="business",
        heating="gas_boiler",
        house=PinnedHouse(
            id="house",
            city="lisbon",
            house_class="business",
            heating="gas_boiler",
            listing_url="https://example.test/house",
            first_seen="2026-10-01",
            last_seen="2026-10-01",
        ),
        records=records,
        norms=load_reference().norms,
    )

    assert {line.item: line.amount_eur for line in cell.lines} == {
        Item.heating: 11.50,
        Item.gas: 13.42,
    }
