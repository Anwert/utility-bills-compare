"""Pin one real house to each class × heating cell of a city.

Contract shared by the classifier (#36) and the monthly pipeline (#38). The
pipeline passes enriched listing candidates and last month's pins; it gets back
the houses to bill and, for every other cell, the reason it stays empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ubc.fetch import ContentFetcher
from ubc.schemas import (
    BuildingCandidate,
    CityConfig,
    HeatingType,
    HouseClass,
    PinnedHouse,
    YearMonth,
)

CellKey = tuple[HouseClass, HeatingType]


@dataclass(frozen=True, slots=True)
class PinResult:
    """``houses`` and ``empty_reasons`` together cover the nine cells exactly once."""

    houses: Mapping[CellKey, PinnedHouse]
    empty_reasons: Mapping[CellKey, str]


def enrich_candidates(
    city: CityConfig,
    candidates: Sequence[BuildingCandidate],
    fetcher: ContentFetcher,
) -> list[BuildingCandidate]:
    """Fill missing building facts from open data. A dead open-data source changes nothing."""

    raise NotImplementedError("#36")


def pin_houses(
    *,
    city: CityConfig,
    candidates: Sequence[BuildingCandidate],
    previous: Sequence[PinnedHouse],
    period: YearMonth,
) -> PinResult:
    """Classify candidates and keep one house per cell, preferring last month's pin."""

    raise NotImplementedError("#36")
