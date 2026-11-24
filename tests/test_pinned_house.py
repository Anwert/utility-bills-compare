"""The classified house shared by the classifier and the cell calculation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ubc.schemas import HeatingType, HouseClass, PinnedHouse


def house(**overrides: object) -> dict:
    data: dict = {
        "id": "otodom-1",
        "city": "warsaw",
        "house_class": "typical_newbuild",
        "heating": "district",
        "housing_fees": [
            {"amount": 1300, "currency": "PLN", "period": "month", "per": "per_dwelling"}
        ],
        "listing_url": "https://example.test/listing/1",
        "address": "пример, не реальный дом",
        "latitude": 52.23,
        "longitude": 21.01,
        "first_seen": "2026-09-01",
        "last_seen": "2026-10-01",
    }
    data.update(overrides)
    return data


def test_pinned_house_keeps_class_heating_fees_and_sightings() -> None:
    parsed = PinnedHouse.model_validate(house())

    assert parsed.house_class is HouseClass.typical_newbuild
    assert parsed.heating is HeatingType.district
    assert parsed.housing_fees[0].amount == 1300
    assert parsed.listing_url == "https://example.test/listing/1"
    assert parsed.first_seen.isoformat() == "2026-09-01"
    assert parsed.last_seen.isoformat() == "2026-10-01"


def test_pinned_house_survives_a_removed_listing() -> None:
    parsed = PinnedHouse.model_validate(
        house(listing_url=None, address="пример", latitude=None, longitude=None, housing_fees=[])
    )

    assert parsed.listing_url is None
    assert parsed.address == "пример"
    assert parsed.housing_fees == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"house_class": "cottage"},
        {"heating": None},
        {"listing_url": "not-a-url"},
        {"latitude": 52.2, "longitude": None},
        {"first_seen": "2026-11-01", "last_seen": "2026-10-01"},
        {"housing_fees": [{"amount": -1, "currency": "PLN", "period": "month", "per": "per_m2"}]},
    ],
)
def test_invalid_pinned_house_is_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        PinnedHouse.model_validate(house(**overrides))
