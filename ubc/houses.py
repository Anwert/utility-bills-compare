"""Pin one real house to each class × heating cell of a city.

Contract shared by the classifier (#36) and the monthly pipeline (#38). The
pipeline passes enriched listing candidates and last month's pins; it gets back
the houses to bill and, for every other cell, the reason it stays empty.

The words and thresholds that decide a cell are ``CityConfig.house_rules``. A
listing whose facts do not decide stays out of the grid instead of being guessed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from ubc.enrichment import Enricher
from ubc.fetch import ContentFetcher
from ubc.schemas import (
    BuildingCandidate,
    CityConfig,
    FeeBasis,
    FeePeriod,
    HeatingType,
    HouseClass,
    HouseClassRule,
    PinnedHouse,
    PriceReference,
    ReferenceApartment,
    YearMonth,
)

CellKey = tuple[HouseClass, HeatingType]

_NOT_FOUND = "дом такого класса с таким отоплением автоматически не найден"
_NO_LISTINGS = "сайты объявлений в этом месяце не дали ни одного дома"


@dataclass(frozen=True, slots=True)
class PinResult:
    """``houses`` and ``empty_reasons`` together cover the nine cells exactly once."""

    houses: Mapping[CellKey, PinnedHouse]
    empty_reasons: Mapping[CellKey, str]


def enrich_candidates(
    city: CityConfig,
    candidates: Sequence[BuildingCandidate],
    fetcher: ContentFetcher,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[BuildingCandidate]:
    """Fill missing building facts from open data. A dead open-data source changes nothing.

    A fact the listing published is never replaced. ``sleep`` and ``clock`` keep
    the one-request-per-second limit; tests pass fakes.
    """

    enricher = Enricher(city, fetcher, sleep=sleep, clock=clock)
    return [enricher.enrich(candidate) for candidate in candidates]


def pin_houses(
    *,
    city: CityConfig,
    candidates: Sequence[BuildingCandidate],
    previous: Sequence[PinnedHouse],
    period: YearMonth,
) -> PinResult:
    """Classify candidates and keep one house per cell, preferring last month's pin.

    A pinned house is replaced only when it dropped out of the data: its own
    listing now belongs to another cell, its column became unavailable in the
    city, or its record is no longer in ``previous``. A listing that vanished,
    or that lost the facts the class was decided on, keeps its house.
    """

    listings = _latest_by_id(city, candidates)
    cells = {candidate_id: _cell(city, item) for candidate_id, item in listings.items()}
    unavailable = city.house_rules.unavailable_heating

    houses: dict[CellKey, PinnedHouse] = {}
    for pinned in _pins_by_cell(city, previous):
        key = (pinned.house_class, pinned.heating)
        now = cells.get(pinned.id)
        if key[1] in unavailable or (now is not None and now != key):
            continue
        houses[key] = _refreshed(pinned, listings.get(pinned.id), period)

    best_first = sorted(listings.values(), key=_preference)
    for candidate in best_first:
        key = cells[candidate.id]
        if key is not None and key not in houses:
            houses[key] = _pinned(candidate, key, period)

    default = _NOT_FOUND if listings else _NO_LISTINGS
    grid = [(row, column) for row in HouseClass for column in HeatingType]
    return PinResult(
        houses={key: houses[key] for key in grid if key in houses},
        empty_reasons={key: unavailable.get(key[1], default) for key in grid if key not in houses},
    )


def _latest_by_id(
    city: CityConfig, candidates: Iterable[BuildingCandidate]
) -> dict[str, BuildingCandidate]:
    latest: dict[str, BuildingCandidate] = {}
    for candidate in candidates:
        if candidate.city is not city.id:
            continue
        known = latest.get(candidate.id)
        if known is None or candidate.seen_at > known.seen_at:
            latest[candidate.id] = candidate
    return latest


def _pins_by_cell(city: CityConfig, previous: Iterable[PinnedHouse]) -> list[PinnedHouse]:
    """One pin per cell. If the state file holds two, the longest-kept house wins."""

    chosen: dict[CellKey, PinnedHouse] = {}
    for pinned in sorted(previous, key=lambda house: (house.first_seen, house.id)):
        if pinned.city is city.id and (pinned.address or pinned.listing_url):
            chosen.setdefault((pinned.house_class, pinned.heating), pinned)
    return list(chosen.values())


def _sighting(candidate: BuildingCandidate, period: YearMonth) -> date:
    """The ad is on the site in ``period`` even when its own edit date is older."""

    return max(candidate.seen_at, period.first_day)


def _refreshed(
    pinned: PinnedHouse, listing: BuildingCandidate | None, period: YearMonth
) -> PinnedHouse:
    if listing is None:
        return pinned
    fresh = _pinned(listing, (pinned.house_class, pinned.heating), period)
    located = fresh.latitude is not None
    return fresh.model_copy(
        update={
            "address": fresh.address or pinned.address,
            "latitude": fresh.latitude if located else pinned.latitude,
            "longitude": fresh.longitude if located else pinned.longitude,
            "first_seen": pinned.first_seen,
            "last_seen": max(pinned.last_seen, fresh.last_seen),
        }
    )


def _pinned(candidate: BuildingCandidate, key: CellKey, period: YearMonth) -> PinnedHouse:
    seen = _sighting(candidate, period)
    fee = candidate.housing_fee
    return PinnedHouse(
        id=candidate.id,
        city=candidate.city,
        house_class=key[0],
        heating=key[1],
        housing_fees=[fee] if fee is not None else [],
        listing_url=candidate.url,
        address=candidate.address,
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        first_seen=seen,
        last_seen=seen,
        note=candidate.note if fee is not None else "",
    )


def _preference(candidate: BuildingCandidate) -> tuple[int, float, int, str]:
    """Most complete first, then closest to the reference flat, newest, and id."""

    facts = (
        candidate.address,
        candidate.latitude,
        candidate.heating or candidate.heating_raw,
        candidate.year_built or candidate.year_from or candidate.year_to,
        candidate.series or candidate.building_type,
        candidate.is_newbuild,
        candidate.floors,
        candidate.area_m2,
        candidate.housing_fee,
        candidate.price_per_m2,
    )
    known = sum(fact is not None for fact in facts)
    target = ReferenceApartment().area_m2
    off = abs(candidate.area_m2 - target) if candidate.area_m2 is not None else float("inf")
    return (-known, off, -candidate.seen_at.toordinal(), candidate.id)


def _cell(city: CityConfig, candidate: BuildingCandidate) -> CellKey | None:
    heating = _heating(city, candidate)
    if heating is None or heating in city.house_rules.unavailable_heating:
        return None
    house_class = _house_class(city, candidate)
    return None if house_class is None else (house_class, heating)


def _heating(city: CityConfig, candidate: BuildingCandidate) -> HeatingType | None:
    if candidate.heating is not None:
        return candidate.heating
    words = {_word(part) for part in (candidate.heating_raw or "").split("|")}
    matched = {
        rule.heating
        for rule in city.house_rules.heatings
        if words.issuperset(_words(rule.all_of)) and words.isdisjoint(_words(rule.none_of))
    }
    return matched.pop() if len(matched) == 1 else None


def _house_class(city: CityConfig, candidate: BuildingCandidate) -> HouseClass | None:
    built_from, _ = _years(candidate)
    if built_from is not None and built_from > candidate.seen_at.year:
        return None
    for rule in city.house_rules.classes:
        if _matches(rule, candidate, city):
            return rule.house_class
    return None


def _matches(rule: HouseClassRule, candidate: BuildingCandidate, city: CityConfig) -> bool:
    if rule.exclude_newbuild and candidate.is_newbuild:
        return False
    if rule.is_newbuild is not None and candidate.is_newbuild is not rule.is_newbuild:
        return False
    if rule.building_types and _word(candidate.building_type) not in _words(rule.building_types):
        return False
    if rule.series and _word(candidate.series) not in _words(rule.series):
        return False
    if rule.block_types:
        block = _word(candidate.block_type)
        if not any(part in block for part in _words(rule.block_types)):
            return False
    built_from, built_to = _years(candidate)
    ratio = _price_ratio(city.house_rules.price_references, candidate)
    return (
        _at_least(built_from, rule.year_from)
        and _at_most(built_to, rule.year_to)
        and _at_least(candidate.floors, rule.floors_from)
        and _at_most(candidate.floors, rule.floors_to)
        and _at_least(ratio, rule.price_ratio_from)
        and _at_most(ratio, rule.price_ratio_to)
        and _at_least(_monthly_fee_per_m2(city, candidate), rule.fee_per_m2_from)
    )


def _at_least(value: float | None, bound: float | None) -> bool:
    return bound is None or (value is not None and value >= bound)


def _at_most(value: float | None, bound: float | None) -> bool:
    return bound is None or (value is not None and value <= bound)


def _years(candidate: BuildingCandidate) -> tuple[int | None, int | None]:
    if candidate.year_built is not None:
        return candidate.year_built, candidate.year_built
    return candidate.year_from, candidate.year_to


def _price_ratio(
    references: Iterable[PriceReference], candidate: BuildingCandidate
) -> float | None:
    price = candidate.price_per_m2
    if price is None or not candidate.address:
        return None
    places = {_word(part) for part in candidate.address.split(",")}
    for reference in references:
        if _word(reference.district) in places and reference.currency == price.currency:
            return price.amount / reference.amount
    return None


def _monthly_fee_per_m2(city: CityConfig, candidate: BuildingCandidate) -> float | None:
    fee = candidate.housing_fee
    if fee is None or fee.currency is not city.currency:
        return None
    amount = fee.amount / 12 if fee.period is FeePeriod.year else fee.amount
    if fee.per is FeeBasis.per_m2:
        return amount
    return amount / candidate.area_m2 if candidate.area_m2 is not None else None


def _word(text: str | None) -> str:
    return (text or "").strip().casefold()


def _words(texts: Iterable[str]) -> set[str]:
    return {_word(text) for text in texts}
