"""Site JSON contract: one city, one month, nine cells."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from ubc.load import load_example_city_month
from ubc.schemas import HEATING_LABELS_RU, HOUSE_CLASS_LABELS_RU, CityMonth, HeatingType, HouseClass


def test_example_snapshot_covers_the_grid_and_one_empty_cell() -> None:
    snapshot = load_example_city_month()

    assert snapshot.city.value == "yerevan"
    assert snapshot.month == "2026-10"
    assert snapshot.apartment.area_m2 == 60
    assert snapshot.exchange_rate.currency.value == "AMD"
    assert snapshot.exchange_rate.per_eur == 400
    assert len(snapshot.cells) == 9
    empty = [cell for cell in snapshot.cells if cell.status.value == "empty"]
    assert len(empty) == 1
    assert empty[0].house_class is HouseClass.soviet_panel
    assert empty[0].heating is HeatingType.district
    assert empty[0].empty_reason
    filled = next(cell for cell in snapshot.cells if cell.status.value == "ok")
    assert filled.total_eur == filled.housing_eur + filled.utility_eur
    assert filled.building is not None
    assert {line.item.value for line in filled.lines} >= {"maintenance", "electricity"}
    assert HOUSE_CLASS_LABELS_RU[HouseClass.business]
    assert HEATING_LABELS_RU[HeatingType.gas_boiler]


def _example() -> dict:
    return load_example_city_month().model_dump(mode="json")


def test_snapshot_rejects_a_missing_cell() -> None:
    data = _example()
    data["cells"] = data["cells"][:-1]

    with pytest.raises(ValidationError, match="9"):
        CityMonth.model_validate(data)


def test_empty_cell_needs_a_reason_and_no_total() -> None:
    data = _example()
    empty = next(cell for cell in data["cells"] if cell["status"] == "empty")
    empty["empty_reason"] = ""

    with pytest.raises(ValidationError):
        CityMonth.model_validate(data)

    data = _example()
    empty = next(cell for cell in data["cells"] if cell["status"] == "empty")
    empty["total_eur"] = 10
    with pytest.raises(ValidationError):
        CityMonth.model_validate(data)


def test_filled_cell_must_add_up() -> None:
    data = _example()
    filled = next(cell for cell in data["cells"] if cell["status"] == "ok")
    filled["lines"][0]["amount_eur"] = 99

    with pytest.raises(ValidationError):
        CityMonth.model_validate(data)


def test_snapshot_rejects_the_wrong_currency() -> None:
    data = _example()
    data["exchange_rate"]["currency"] = "EUR"
    data["exchange_rate"]["per_eur"] = 1

    with pytest.raises(ValidationError):
        CityMonth.model_validate(copy.deepcopy(data))
