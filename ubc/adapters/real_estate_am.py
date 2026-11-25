"""Yerevan apartment listings from real-estate.am.

The property sitemap lists every ad; paging the site itself goes through
``/filtered-properties``, which robots.txt forbids. 2-room sale ads in Yerevan are
taken newest first, one district after another, so the old fund of the outer
districts is not lost behind the centre's new buildings. Each ad carries one
escaped JSON object: title and description, building type, market, new-construction
flag, finish, price, area, floors, utilities (pool and gym among them), address. There
is no year and no maintenance fee. Heating stays unmapped.
"""

from __future__ import annotations

import json
import re
from datetime import date

from ubc.adapter import AdapterOutput, CollectContext, register_adapter
from ubc.adapters.listing_common import (
    SitePages,
    Sleep,
    collect_ads,
    readable,
    round_robin,
    search_url,
)
from ubc.adapters.listing_text import read_text
from ubc.schemas import Amenity, BuildingCandidate, SalePrice

_HREF = re.compile(
    r'href="(https://www\.real-estate\.am/en/prp/apartment/buy/yerevan/'
    r'([^"/]+)/[^"]*2-room/(\d+))"'
)
_HEAT = frozenset({"centralized_heating", "heating", "gas"})
_AMENITY_TAGS = {"swimming_pool": Amenity.swimming_pool, "gym": Amenity.gym}
_SHELL = "START"
# The closing quote of a field is the one without a backslash before it.
_TEXT_FIELD = re.compile(r'"(?:title|description)(?:Hy|Ru|En)":"(.*?)(?<!\\)"', re.DOTALL)


class RealEstateAmAdapter:
    """Yerevan apartments listed on real-estate.am."""

    name = "real_estate_am"

    def __init__(self, sleep: Sleep | None = None) -> None:
        self._sleep = sleep

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        pages = SitePages(ctx, self._sleep)
        urls = _listing_urls(pages.text(search_url(ctx)))
        return collect_ads(ctx, pages, urls, _candidate)


def _listing_urls(sitemap: str) -> list[str]:
    ads = {url: (district, int(number)) for url, district, number in _HREF.findall(sitemap)}
    districts: dict[str, list[str]] = {}
    for url, (district, _) in sorted(ads.items(), key=lambda ad: -ad[1][1]):
        districts.setdefault(district, []).append(url)
    return round_robin(districts.values())


def _candidate(ctx: CollectContext, url: str, html: str) -> BuildingCandidate | None:
    text = html.replace('\\"', '"')
    start = text.find('"buildingType"')
    if start < 0:
        return None
    body = text[start:]
    building_type = _string(body, "buildingType")
    if not building_type or not _is_live_sale(text[:start], body):
        return None
    updated = re.search(r'"updatedAt":"(\d{4}-\d{2}-\d{2})', body)
    seen = date.fromisoformat(updated.group(1)) if updated else ctx.period.first_day
    coords = re.search(r'"address":\{"latitude":([\d.]+),"longitude":([\d.]+)', body)
    listing_id = _number_text(body, "id")
    tags = [str(item.get("title")) for item in _utilities(body)]
    heat = [tag for tag in tags if tag in _HEAT]
    complex_tags = {_AMENITY_TAGS.get(tag) for tag in tags}
    ad_text = read_text("\n".join([*_texts(text[:start]), *tags]))
    state = _string(body, "propertyState")
    return BuildingCandidate(
        id=f"real-estate-{listing_id}",
        city=ctx.city.id,
        address=_address(body),
        latitude=float(coords.group(1)) if coords else None,
        longitude=float(coords.group(2)) if coords else None,
        heating_raw=" | ".join([*heat, *ad_text.heating]) or None,
        series=ad_text.series,
        building_type=building_type,
        is_newbuild=_flag(body, "isNewConstruction"),
        floor=_int(body, "floor"),
        floors=_int(body, "totalFloor"),
        amenities=[item for item in Amenity if item in complex_tags or item in ad_text.amenities],
        unfinished=None if state is None else state == _SHELL,
        area_m2=_float(body, "totalArea"),
        price_per_m2=_price(text[:start], body),
        url=url,
        seen_at=seen,
        note=_string(body, "marketType") or "",
    )


def _is_live_sale(head: str, body: str) -> bool:
    actions = re.findall(r'"propertyActionType":"([A-Z]+)"', head)
    return (
        actions[-1:] == ["SALE"]
        and _string(body, "propertyStatus") == "ACTIVE"
        and _flag(body, "removed") is False
    )


def _price(head: str, body: str) -> SalePrice | None:
    """The page shows ``updatedPrice``; ``price`` is what the ad first asked."""

    currencies = re.findall(r'"currency":"([A-Z]{3})"', head)
    total = _float(body, "updatedPrice") or _float(body, "price")
    area = _float(body, "totalArea")
    if not currencies or not total or not area:
        return None
    return SalePrice(amount=round(total / area, 2), currency=currencies[-1])


def _texts(head: str) -> list[str]:
    """Title and description in every language the ad has."""

    start = head.rfind('"property":{')
    if start < 0:
        return []
    return [_unescaped(field) for field in _TEXT_FIELD.findall(head[start:])]


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
    return readable(_unescaped(match.group(1))) or None


def _unescaped(text: str) -> str:
    """The page escapes this JSON inside another JSON string."""

    return str(json.loads('"' + text.replace("\\\\", "\\") + '"', strict=False))


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
