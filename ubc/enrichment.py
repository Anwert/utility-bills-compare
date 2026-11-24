"""Open registers that fill the facts a listing left out.

Which registers a city uses is ``CityConfig.open_data``. Every failure here means
"no enrichment": a register that failed twice is not asked again in this run. The
OSM construction year is not used, under 2% of buildings have one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, quote_plus

from ubc.fetch import ContentFetcher, FetchError
from ubc.schemas import BuildingCandidate, CityConfig, OpenDataKind

_MAX_FAILURES = 2
# Nominatim's usage policy allows one request a second. The others get the same pace.
_INTERVAL_S = 1.0
_OSM_RADIUS_M = 15
_NOMINATIM_BUILDING_RANK = 30
# Half of the WFS search box in degrees: about 15 m for a building, 30 m for a block.
_BUILDING_BOX = 0.00015
_BLOCK_BOX = 0.0003
_ALKIS_LAYER = "alkis_gebaeude:gebaeude"
_AGE_LAYER = "ua_gebaeudealter:ua_gebaeudealter_baualter"
# A postcode has five or six digits (German, Armenian) and is not a house number.
_HOUSE_NUMBER = re.compile(r"(?<!\d)\d{1,4}(?!\d)")
_DECADE = re.compile(r"(?:bis (\d{4})|(\d{4})-(\d{4}))")
_FAILURES = (FetchError, OSError, ValueError, KeyError, TypeError, AttributeError)

Point = tuple[float, float]


class Enricher:
    """One run over one city's candidates. Remembers geocoded addresses and dead sources."""

    def __init__(
        self,
        city: CityConfig,
        fetcher: ContentFetcher,
        *,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self._city = city
        self._fetcher = fetcher
        self._sleep = sleep
        self._clock = clock
        self._urls = {source.kind: source.url for source in city.open_data if source.enabled}
        self._failures = dict.fromkeys(OpenDataKind, 0)
        self._points: dict[str, Point | None] = {}
        self._allowed_at = dict.fromkeys(OpenDataKind, 0.0)

    def enrich(self, candidate: BuildingCandidate) -> BuildingCandidate:
        if not any(self._missing(candidate, kind) for kind in self._registers()):
            return candidate
        candidate = self._located(candidate)
        if candidate.latitude is None or candidate.longitude is None:
            return candidate
        point = (candidate.latitude, candidate.longitude)
        for kind, ask in self._registers().items():
            if self._missing(candidate, kind):
                facts = self._try(kind, ask, point, candidate.address)
                candidate = _filled(candidate, facts or {})
        return candidate

    def _registers(self) -> dict[OpenDataKind, Callable[..., dict[str, Any]]]:
        known = {
            OpenDataKind.berlin_alkis: self._alkis,
            OpenDataKind.berlin_building_age: self._building_age,
            OpenDataKind.osm_levels: self._osm_levels,
        }
        return {kind: ask for kind, ask in known.items() if kind in self._urls}

    @staticmethod
    def _missing(candidate: BuildingCandidate, kind: OpenDataKind) -> bool:
        if kind is OpenDataKind.berlin_building_age:
            return candidate.block_type is None or not _has_year(candidate)
        return candidate.floors is None

    def _try(self, kind: OpenDataKind, ask: Callable[..., Any], *args: object) -> Any:
        if kind not in self._urls or self._failures[kind] >= _MAX_FAILURES:
            return None
        self._wait_for_turn(kind)
        try:
            return ask(*args)
        except _FAILURES:
            self._failures[kind] += 1
            return None

    def _wait_for_turn(self, kind: OpenDataKind) -> None:
        now = self._clock()
        if now < self._allowed_at[kind]:
            self._sleep(self._allowed_at[kind] - now)
        self._allowed_at[kind] = self._clock() + _INTERVAL_S

    def _located(self, candidate: BuildingCandidate) -> BuildingCandidate:
        address = candidate.address
        if candidate.latitude is not None or not address or not _HOUSE_NUMBER.search(address):
            return candidate
        if address not in self._points:
            self._points[address] = self._try(OpenDataKind.nominatim, self._geocode, address)
        point = self._points[address]
        if point is None:
            return candidate
        return candidate.model_copy(update={"latitude": point[0], "longitude": point[1]})

    def _geocode(self, address: str) -> Point | None:
        query = address
        if self._city.name_en.casefold() not in address.casefold():
            query = f"{address}, {self._city.name_en}"
        url = self._urls[OpenDataKind.nominatim]
        found = json.loads(
            self._fetcher.fetch_text(f"{url}?format=jsonv2&limit=1&q={quote(query)}")
        )
        if not found or int(found[0]["place_rank"]) < _NOMINATIM_BUILDING_RANK:
            return None
        return float(found[0]["lat"]), float(found[0]["lon"])

    def _osm_levels(self, point: Point, address: str | None) -> dict[str, Any]:
        del address
        query = (
            f"[out:json][timeout:30];way(around:{_OSM_RADIUS_M},{point[0]},{point[1]})"
            '["building"];out tags;'
        )
        answer = json.loads(
            self._fetcher.post_text(
                self._urls[OpenDataKind.osm_levels],
                f"data={quote_plus(query)}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        )
        levels = {
            element["tags"]["building:levels"]
            for element in answer["elements"]
            if "building:levels" in element["tags"]
        }
        if len(levels) != 1:
            return {}
        text = levels.pop()
        return {"floors": int(text)} if text.isdecimal() and int(text) >= 1 else {}

    def _alkis(self, point: Point, address: str | None) -> dict[str, Any]:
        features = self._wfs(OpenDataKind.berlin_alkis, _ALKIS_LAYER, point, _BUILDING_BOX)
        named = [item for item in features if _has_address(item["properties"], address)]
        found = named[0] if len(named) == 1 else _containing(features, point)
        floors = found["properties"]["aog"] if found is not None else None
        return {"floors": int(floors)} if floors else {}

    def _building_age(self, point: Point, address: str | None) -> dict[str, Any]:
        del address
        features = self._wfs(OpenDataKind.berlin_building_age, _AGE_LAYER, point, _BLOCK_BOX)
        block = _containing(features, point)
        if block is None:
            return {}
        facts: dict[str, Any] = {"block_type": block["properties"]["typklar"] or None}
        decade = _DECADE.fullmatch(block["properties"]["ueberw_dekade_woh_neu"] or "")
        if decade is not None:
            until, start, end = decade.groups()
            facts["year_from"] = int(start) if start else None
            facts["year_to"] = int(until or end)
        return facts

    def _wfs(
        self, kind: OpenDataKind, layer: str, point: Point, half: float
    ) -> list[dict[str, Any]]:
        latitude, longitude = point
        box = (
            f"{latitude - half:.6f},{longitude - half:.6f},"
            f"{latitude + half:.6f},{longitude + half:.6f}"
        )
        answer = json.loads(
            self._fetcher.fetch_text(
                f"{self._urls[kind]}?service=WFS&version=1.1.0&request=GetFeature"
                f"&typeName={layer}&outputFormat=application/json&srsName=EPSG:4326"
                f"&bbox={box},urn:ogc:def:crs:EPSG:4326"
            )
        )
        return answer["features"]


def _filled(candidate: BuildingCandidate, facts: dict[str, Any]) -> BuildingCandidate:
    """Add ``facts`` the listing did not publish. Its years are one fact."""

    if _has_year(candidate):
        facts = {name: fact for name, fact in facts.items() if not name.startswith("year_")}
    missing = {name: fact for name, fact in facts.items() if getattr(candidate, name) is None}
    return candidate.model_copy(update=missing)


def _has_year(candidate: BuildingCandidate) -> bool:
    years = (candidate.year_built, candidate.year_from, candidate.year_to)
    return any(year is not None for year in years)


def _has_address(properties: dict[str, Any], address: str | None) -> bool:
    """``namlag`` reads "Street A 21; Street B 30, 31"."""

    wanted = re.match(r"\s*(.+?)\s+(\d+\s*[a-z]?)\s*(?:,|$)", address or "", re.IGNORECASE)
    if wanted is None:
        return False
    street, number = wanted.group(1).casefold(), wanted.group(2).casefold()
    for place in (properties["namlag"] or "").casefold().split(";"):
        name, _, rest = place.strip().partition(f"{street} ")
        if not name and number in {item.strip() for item in rest.split(",")}:
            return True
    return False


def _containing(features: list[dict[str, Any]], point: Point) -> dict[str, Any] | None:
    for feature in features:
        shape = feature["geometry"]
        polygons = [shape["coordinates"]] if shape["type"] == "Polygon" else shape["coordinates"]
        if any(_inside(polygon[0], point) for polygon in polygons):
            return feature
    return None


def _inside(ring: list[list[float]], point: Point) -> bool:
    """Ray casting. GeoJSON vertices are longitude, latitude."""

    latitude, longitude = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False):
        if (y1 > latitude) != (y2 > latitude):
            crossing = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
            if longitude < crossing:
                inside = not inside
    return inside
