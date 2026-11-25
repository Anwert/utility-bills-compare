"""Yerevan 2-room sale listings from myrealty.am.

The sale category links the ads of the old fund (``bnakaran``) and of new
buildings (``norakaruyc-bnakaran``) together, page by page. Half of the cap goes
to each, so a run of new buildings does not push the old fund out. Each ad is
HTML: building type, condition, area, floor/storeys, facilities, additional
information (pool and gym among it), free text, price per m², map point, and an
edit date. Year and the maintenance fee are not on the page. Heating stays
unmapped. An ad that is gone redirects to the home page.

``settings.max_pages`` caps the search pages (6 by default).
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape
from urllib.parse import unquote_plus, urlparse

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_listing
from ubc.adapters.listing_common import (
    SitePages,
    Sleep,
    collect_ads,
    max_ads,
    readable,
    round_robin,
    search_url,
)
from ubc.adapters.listing_text import read_text
from ubc.fetch import FetchError
from ubc.schemas import Amenity, BuildingCandidate, SalePrice

_LISTING_HREF = re.compile(
    r'href="(?:https://myrealty\.am)?/(?:hy|en)/'
    r'((bnakaran|norakaruyc-bnakaran)/2-senyakanoc/[^"]+/Yerevan/\d+)"'
)
_PAGE_HREF = re.compile(r'href="(https://myrealty\.am/[^"?]+\?page=(\d+))"')
_TITLE = re.compile(r"<title>([^<]*)</title>")
_EDITED = re.compile(r"Edited in</label>\s*<i[^>]*>\s*</i>\s*(\d{2})\.(\d{2})\.(\d{4})")
_COORDS = re.compile(r'data-lat="([0-9.]+)"\s*data-lng="([0-9.]+)"')
_FACILITIES = "Facilities"
_ADDITIONAL = "Additional information"
_INFORMATION = re.compile(
    r'<h4 class="item-view-block-title">information</h4>\s*<pre>(.*?)</pre>', re.DOTALL
)
_AMENITY_TAGS = {"Swimming pool": Amenity.swimming_pool, "Gym": Amenity.gym}
_SHELL = "Zero condition"
_HEAT_WORDS = frozenset({"Heating", "Gas", "Central heating"})
_PRICE = re.compile(
    r"<label>\s*Price \(SQ\. M\.\)\s*</label>(?:(?!<label>).)*?"
    r'<span>\s*<i class="icon-([a-z]{3})-syb"></i>\s*([\d,]+)\s*</span>',
    re.DOTALL,
)
_ORIGIN = "https://myrealty.am"
_OLD_FUND = "bnakaran"
_NEW_BUILDINGS = "norakaruyc-bnakaran"
_MAX_PAGES = 6


class MyrealtyAdapter:
    """2-room Yerevan apartments listed on myrealty.am."""

    name = "myrealty"

    def __init__(self, sleep: Sleep | None = None) -> None:
        self._sleep = sleep

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        pages = SitePages(ctx, self._sleep)
        return collect_ads(ctx, pages, _listing_urls(ctx, pages), _candidate)


def _listing_urls(ctx: CollectContext, pages: SitePages) -> list[str]:
    """Ads of both kinds, taken in turn, from as many search pages as needed."""

    found: dict[str, list[str]] = {_OLD_FUND: [], _NEW_BUILDINGS: []}
    half = (max_ads(ctx) + 1) // 2
    last_page = int(require_listing(ctx).settings.get("max_pages", _MAX_PAGES))
    html = pages.text(search_url(ctx))
    for number in range(2, last_page + 2):
        for path, kind in _LISTING_HREF.findall(html):
            url = f"{_ORIGIN}/en/{unescape(path)}"
            if url not in found[kind]:
                found[kind].append(url)
        if all(len(urls) >= half for urls in found.values()) or number > last_page:
            break
        following = [href for href, page in _PAGE_HREF.findall(html) if int(page) == number]
        if not following:
            break
        try:
            html = pages.text(following[0])
        except FetchError:
            break
    return round_robin(found.values())


def _candidate(ctx: CollectContext, url: str, html: str) -> BuildingCandidate | None:
    parts = urlparse(url).path.strip("/").split("/")
    kind, listing_id = parts[1], parts[-1]
    title = _TITLE.search(html)
    if title is None or listing_id not in title.group(1) or "for sale" not in title.group(1):
        return None
    building_type = _span(html, "Building type")
    area_text = _span(html, "Area")
    if building_type is None and area_text is None:
        return None
    coords = _COORDS.search(html)
    edited = _EDITED.search(html)
    seen = (
        date(int(edited.group(3)), int(edited.group(2)), int(edited.group(1)))
        if edited
        else ctx.period.first_day
    )
    information = _INFORMATION.search(html)
    text = read_text(unescape(information.group(1)) if information else "")
    heat = [name for name in _tags(html, _FACILITIES) if name in _HEAT_WORDS]
    tags = {_AMENITY_TAGS.get(name) for name in _tags(html, _ADDITIONAL)}
    condition = _span(html, "condition")
    floor_text = _span(html, "Floor/Storeys")
    return BuildingCandidate(
        id=f"myrealty-{listing_id}",
        city=ctx.city.id,
        address=", ".join(readable(unquote_plus(part)) for part in parts[3:-1]),
        latitude=float(coords.group(1)) if coords else None,
        longitude=float(coords.group(2)) if coords else None,
        heating_raw=" | ".join([*heat, *text.heating]) or None,
        series=text.series,
        building_type=building_type,
        is_newbuild=True if kind == _NEW_BUILDINGS else None,
        floor=_floor(floor_text),
        floors=_storeys(floor_text),
        amenities=[item for item in Amenity if item in tags or item in text.amenities],
        unfinished=None if condition is None else condition == _SHELL,
        area_m2=_number(area_text),
        price_per_m2=_price(html),
        url=url,
        seen_at=seen,
    )


def _span(html: str, label: str) -> str | None:
    found = re.search(
        rf"<label>\s*{re.escape(label)}\s*</label>(?:(?!<label>).)*?<span>\s*([^<]+?)\s*</span>",
        html,
        re.DOTALL,
    )
    if found is None:
        return None
    return found.group(1).strip() or None


def _tags(html: str, title: str) -> list[str]:
    block = re.search(rf'<h4 class="item-view-block-title">{title}</h4>(.*?)</ul>', html, re.DOTALL)
    if block is None:
        return []
    return [name.strip() for name in re.findall(r"<label>([^<]+)</label>", block.group(1))]


def _price(html: str) -> SalePrice | None:
    found = _PRICE.search(html)
    if found is None:
        return None
    return SalePrice(amount=float(found.group(2).replace(",", "")), currency=found.group(1).upper())


def _floor(text: str | None) -> int | None:
    match = re.match(r"(\d+)/", text or "")
    return int(match.group(1)) if match else None


def _storeys(text: str | None) -> int | None:
    if not text:
        return None
    if "/" in text:
        text = text.split("/")[-1]
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _number(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


myrealty = register_adapter(MyrealtyAdapter())
