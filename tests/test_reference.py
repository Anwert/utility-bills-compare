"""Reference apartment and consumption-norm stubs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ubc.load import load_reference
from ubc.schemas import ConsumptionNorm, ReferenceApartment


def test_reference_apartment_is_the_shared_dwelling() -> None:
    reference = load_reference()

    assert reference.apartment.rooms == 2
    assert reference.apartment.area_m2 == 60
    assert reference.apartment.occupants == 2
    assert reference.norms
    assert all(norm.source == "stub" for norm in reference.norms)


def test_apartment_shape_is_fixed() -> None:
    with pytest.raises(ValidationError):
        ReferenceApartment.model_validate({"rooms": 3, "area_m2": 60, "occupants": 2})


def test_negative_norm_is_rejected() -> None:
    sample = load_reference().norms[0].model_dump(mode="json")
    sample["annual_value"] = -5

    with pytest.raises(ValidationError):
        ConsumptionNorm.model_validate(sample)
