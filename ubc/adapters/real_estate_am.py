"""Yerevan apartment listings from real-estate.am.

The index page links the ads. Each ad carries one escaped JSON object:
building type, market, new-construction flag, area, floors, utilities,
address. There is no year and no maintenance fee. Heating stays unmapped.
"""

from __future__ import annotations

import json
import re
from datetime import date

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_listing
from ubc.schemas import BuildingCandidate

_HREF = re.compile(r'href="((?:https://www\.real-estate\.am)?/en/prp/apartment/buy/yerevan/[^"]+)"')
_HEAT = frozenset({"centralized_heating", "heating", "gas"})
_ORIGIN = "https://www.real-estate.am"


class RealEstateAmAdapter:
    """Yerevan apartments listed on real-estate.am."""

    name = "real_estate_am"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        site = require_listing(ctx)
        if not site.url:
            raise ValueError("real_estate_am requires a search url")
        urls = _listing_urls(ctx.fetcher.fetch_text(site.url))
        candidates = []
        for url in urls:
            candidate = _candidate(ctx, url, ctx.fetcher.fetch_text(url))
            if candidate is not None:
                candidates.append(candidate)
        if urls and not candidates:
            raise ValueError("real_estate_am found no listing fields")
        return AdapterOutput(candidates=candidates)


def _listing_urls(html: str) -> list[str]:
    found: list[str] = []
    for href in _HREF.findall(html):
        if href.startswith("/"):
            href = _ORIGIN + href
        if href not in found:
            found.append(href)
    return found


def _candidate(ctx: CollectContext, url: str, html: str) -> BuildingCandidate | None:
    text = html.replace('\\"', '"')
    start = text.find('"buildingType"')
    if start < 0:
        return None
    body = text[start:]
    building_type = _string(body, "buildingType")
    if not building_type:
        return None
    updated = re.search(r'"updatedAt":"(\d{4}-\d{2}-\d{2})', body)
    seen = date.fromisoformat(updated.group(1)) if updated else ctx.period.first_day
    coords = re.search(r'"address":\{"latitude":([\d.]+),"longitude":([\d.]+)', body)
    listing_id = _number_text(body, "id")
    heat = [item["title"] for item in _utilities(body) if item.get("title") in _HEAT]
    return BuildingCandidate(
        id=f"real-estate-{listing_id}",
        city=ctx.city.id,
        address=_address(body),
        latitude=float(coords.group(1)) if coords else None,
        longitude=float(coords.group(2)) if coords else None,
        heating_raw=" | ".join(heat) or None,
        building_type=building_type,
        is_newbuild=_flag(body, "isNewConstruction"),
        floors=_int(body, "totalFloor"),
        area_m2=_float(body, "totalArea"),
        url=url,
        seen_at=seen,
        note=_string(body, "marketType") or "",
    )


def _utilities(body: str) -> list[dict[str, object]]:
    match = re.search(r'"utilities":(\[.*?\]),"branch"', body)
    if match is None:
        return []
    parsed = json.loads(match.group(1))
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _address(body: str) -> str | None:
    parts = [
        _title(body, "street"),
        _title(body, "district"),
        _title(body, "region"),
    ]
    text = ", ".join(part for part in parts if part)
    return text or None


def _title(body: str, key: str) -> str | None:
    match = re.search(rf'"{key}":\{{[^}}]*"titleEn":"([^"]*)"', body)
    if match is None or not match.group(1):
        return None
    return match.group(1)


def _string(body: str, name: str) -> str | None:
    match = re.search(rf'"{name}":"([^"]*)"', body)
    if match is None or not match.group(1):
        return None
    return match.group(1)


def _flag(body: str, name: str) -> bool | None:
    match = re.search(rf'"{name}":(true|false)', body)
    if match is None:
        return None
    return match.group(1) == "true"


def _int(body: str, name: str) -> int | None:
    text = _number_text(body, name)
    return int(text) if text is not None else None


def _float(body: str, name: str) -> float | None:
    text = _number_text(body, name)
    return float(text) if text is not None else None


def _number_text(body: str, name: str) -> str | None:
    match = re.search(rf'"{name}":(\d+(?:\.\d+)?)', body)
    return match.group(1) if match else None


real_estate_am = register_adapter(RealEstateAmAdapter())
