"""When a published figure should carry the city's paid-data sentence.

Statistics and third-party figures, including those standing in for the
disabled LLM parser, show ``CityConfig.paid_data_note``. The wording and the
dollar amount live in the city file. Official figures, including a direct
proxy fetch of an official page, do not.
"""

from __future__ import annotations

from ubc.schemas import CityConfig, Reliability


def paid_data_note(city: CityConfig, reliability: Reliability) -> str | None:
    """The configured sentence, or None when this reliability does not show it."""

    if reliability in (Reliability.statistics, Reliability.third_party):
        return city.paid_data_note
    return None
