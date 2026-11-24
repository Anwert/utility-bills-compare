"""Open data fills what a listing left out. Responses were saved on 25.11.2026."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ubc.fetch import FetchError, FixtureFetcher
from ubc.houses import enrich_candidates
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import BuildingCandidate

_ROOT = REPO_ROOT / "tests" / "fixtures" / "open_data"
_OVERPASS = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"


class RecordingFetcher(FixtureFetcher):
    """Saved responses, plus the list of calls. ``overpass`` picks the POST answer."""

    def __init__(self, overpass: str | None = None) -> None:
        super().__init__(_ROOT)
        if overpass is not None:
            self.manifest = {**self.manifest, _OVERPASS: overpass}
        self.calls: list[str] = []

    def fetch_text(self, url: str) -> str:
        self.calls.append(url)
        return super().fetch_text(url)


class DeadFetcher(FixtureFetcher):
    def __init__(self, error: Exception) -> None:
        super().__init__(Path("."), {})
        self.error = error
        self.calls = 0

    def fetch_text(self, url: str) -> str:
        self.calls += 1
        raise self.error


class Clock:
    """Time that moves only when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def candidate(city: str, **facts: object) -> BuildingCandidate:
    data: dict = {
        "id": f"{city}-1",
        "city": city,
        "url": f"https://example.test/{city}/1",
        "seen_at": "2026-11-24",
    }
    data.update(facts)
    return BuildingCandidate.model_validate(data)


def enrich(fetcher: FixtureFetcher, *candidates: BuildingCandidate, clock: Clock | None = None):
    clock = clock or Clock()
    city = load_city(candidates[0].city.value)
    return enrich_candidates(city, candidates, fetcher, sleep=clock.sleep, clock=clock)


def test_wuehlischstrasse_30_gets_its_floors_decade_and_block_type() -> None:
    fetcher = RecordingFetcher()

    [house] = enrich(fetcher, candidate("berlin", address="Wühlischstraße 30, 10245 Berlin"))

    assert (house.latitude, house.longitude) == (52.5096443, 13.4577523)
    assert house.floors == 5
    # The block that contains the house. Its neighbour across the street is "bis 1900".
    assert (house.year_from, house.year_to) == (1901, 1910)
    assert house.year_built is None
    assert house.block_type == "Geschlossene Blockbebauung, Hinterhof (1870er-1918), 5-geschossig"


def test_a_block_up_to_1900_has_no_lower_year() -> None:
    [house] = enrich(
        RecordingFetcher(),
        candidate("berlin", address="Wühlischstraße 37", latitude=52.5088, longitude=13.4588),
    )

    assert (house.year_from, house.year_to) == (None, 1900)
    assert house.floors == 5


def test_facts_from_the_listing_are_never_replaced() -> None:
    fetcher = RecordingFetcher()
    known = candidate(
        "berlin",
        address="Wühlischstraße 37",
        latitude=52.5088,
        longitude=13.4588,
        year_built=2005,
        floors=7,
    )

    [house] = enrich(fetcher, known)

    assert (house.year_built, house.year_from, house.year_to) == (2005, None, None)
    assert house.floors == 7
    assert house.block_type is not None


def test_osm_levels_fill_missing_floors() -> None:
    [house] = enrich(
        RecordingFetcher(overpass="overpass_yerevan_levels.json"),
        candidate("yerevan", latitude=40.1643421, longitude=44.51712),
    )

    assert house.floors == 5
    assert house.year_built is None


def test_osm_building_under_construction_gives_no_floors() -> None:
    [house] = enrich(
        RecordingFetcher(overpass="overpass_yerevan_construction.json"),
        candidate("yerevan", latitude=40.208024, longitude=44.511803),
    )

    assert house.floors is None


def test_neighbouring_osm_buildings_of_different_height_decide_nothing(tmp_path: Path) -> None:
    levels = [{"tags": {"building": "apartments", "building:levels": text}} for text in "59"]
    (tmp_path / "two.json").write_text(json.dumps({"elements": levels}), encoding="utf-8")

    [house] = enrich(
        FixtureFetcher(tmp_path, {_OVERPASS: "two.json"}),
        candidate("yerevan", latitude=40.1643421, longitude=44.51712),
    )

    assert house.floors is None


def test_nothing_is_requested_when_the_listing_already_has_the_floors() -> None:
    fetcher = RecordingFetcher()
    listing = candidate("yerevan", address="M.Khorenatsi St 47/1, Yerevan", floors=9)

    assert enrich(fetcher, listing) == [listing]
    assert fetcher.calls == []


def test_an_address_without_a_house_number_is_not_geocoded() -> None:
    fetcher = RecordingFetcher()
    listing = candidate("berlin", address="10245 Berlin")

    assert enrich(fetcher, listing) == [listing]
    assert fetcher.calls == []


def test_nominatim_is_asked_at_most_once_a_second_and_once_per_address() -> None:
    clock = Clock()
    fetcher = RecordingFetcher()
    address = "Wühlischstraße 30, 10245 Berlin"
    other = "Wühlischstraße 31, 10245 Berlin"

    enrich(
        fetcher,
        candidate("berlin", id="a", address=address),
        candidate("berlin", id="b", address=address),
        candidate("berlin", id="c", address=other),
        clock=clock,
    )

    asked = [url for url in fetcher.calls if "nominatim" in url]
    assert len(asked) == 2
    assert sum(clock.slept) >= 1.0


@pytest.mark.parametrize(
    "error",
    [FetchError("https://example.test", "HTTP failed"), TimeoutError(), ValueError("not json")],
)
def test_a_dead_source_changes_nothing_and_is_not_asked_for_every_listing(
    error: Exception,
) -> None:
    fetcher = DeadFetcher(error)
    listings = [
        candidate("yerevan", id=f"y-{index}", latitude=40.1 + index / 100, longitude=44.5)
        for index in range(10)
    ]

    assert enrich(fetcher, *listings) == listings
    assert fetcher.calls == 2


def test_an_html_error_page_or_an_unexpected_shape_changes_nothing(tmp_path: Path) -> None:
    listing = candidate("yerevan", latitude=40.1643421, longitude=44.51712)
    for body in ("<html>504 Gateway Timeout</html>", "[]", '{"elements": [null]}', '{"a": 1}'):
        (tmp_path / "bad.json").write_text(body, encoding="utf-8")

        assert enrich(FixtureFetcher(tmp_path, {_OVERPASS: "bad.json"}), listing) == [listing]


def test_a_disabled_source_is_not_called() -> None:
    city = load_city("yerevan")
    for source in city.open_data:
        source.enabled = False
    fetcher = RecordingFetcher(overpass="overpass_yerevan_levels.json")
    listing = candidate("yerevan", latitude=40.1643421, longitude=44.51712)

    assert enrich_candidates(city, [listing], fetcher) == [listing]
    assert fetcher.calls == []
