"""Building candidates from listings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ubc.schemas import BuildingCandidate


def candidate(**overrides: object) -> dict:
    data: dict = {
        "id": "halooglasi-1",
        "city": "belgrade",
        "address": "Ugrinovacka",
        "latitude": 44.85,
        "longitude": 20.4,
        "heating_raw": "CG",
        "heating": "district",
        "year_built": None,
        "year_from": 1955,
        "year_to": 2000,
        "series": None,
        "is_newbuild": False,
        "floors": 5,
        "area_m2": 53.4,
        "housing_fee": {
            "amount": 29.82,
            "currency": "RSD",
            "period": "month",
            "per": "per_m2",
        },
        "url": "https://example.test/listing/1",
        "seen_at": "2026-11-24",
        "note": "",
    }
    data.update(overrides)
    return data


def test_listing_with_partial_facts_is_a_valid_candidate() -> None:
    parsed = BuildingCandidate.model_validate(candidate(year_built=None, heating=None))

    assert parsed.heating is None
    assert parsed.heating_raw == "CG"
    assert parsed.housing_fee is not None
    assert parsed.housing_fee.period.value == "month"
    assert parsed.year_from == 1955


def test_candidate_may_omit_everything_the_listing_did_not_publish() -> None:
    parsed = BuildingCandidate.model_validate(
        {
            "id": "myhome-1",
            "city": "tbilisi",
            "url": "https://example.test/listing/2",
            "seen_at": "2026-11-24",
        }
    )

    assert parsed.address is None
    assert parsed.housing_fee is None
    assert parsed.area_m2 is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": ""},
        {"url": "ftp://example.test/x"},
        {"id": ""},
        {"latitude": 44.8, "longitude": None},
        {"latitude": 100, "longitude": 20},
        {"year_from": 2010, "year_to": 1990},
        {"year_built": 1700},
        {"floors": 0},
        {"area_m2": 0},
        {"housing_fee": {"amount": -1, "currency": "EUR", "period": "month", "per": "per_m2"}},
        {"city": "all"},
    ],
)
def test_invalid_candidate_is_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        BuildingCandidate.model_validate(candidate(**overrides))
