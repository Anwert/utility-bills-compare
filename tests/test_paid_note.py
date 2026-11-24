"""The paid-data sentence shown for statistics and third-party figures."""

from __future__ import annotations

from ubc.load import load_city
from ubc.notes import paid_data_note
from ubc.schemas import Reliability

_CUSTOM = "оценка; точные данные поставщика возможны за доплату (~$7/мес.)"


def test_the_note_text_comes_from_the_city_configuration() -> None:
    city = load_city("lisbon").model_copy(update={"paid_data_note": _CUSTOM})

    assert paid_data_note(city, Reliability.statistics) == _CUSTOM
    assert paid_data_note(city, Reliability.third_party) == _CUSTOM
    assert paid_data_note(city, Reliability.official) is None
