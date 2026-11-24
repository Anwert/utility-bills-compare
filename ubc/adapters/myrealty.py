"""Yerevan 2-room sale listings from myrealty.am.

The search page links the ads. Each ad is HTML: building type, area,
floor/storeys, facilities, map point, and an edit date. Year and the
maintenance fee are not on the page. Heating stays unmapped.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote, urlparse

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_listing
from ubc.schemas import BuildingCandidate

_LISTING_HREF = re.compile(
    r'href="((?:https://myrealty\.am)?/(?:hy|en)/bnakaran/2-senyakanoc/[^"]+)"'
)
_EDITED = re.compile(r"Edited in</label>\s*<i[^>]*>\s*</i>\s*(\d{2})\.(\d{2})\.(\d{4})")
_COORDS = re.compile(r'data-lat="([0-9.]+)"\s*data-lng="([0-9.]+)"')
_FACILITIES = re.compile(
    r'<h4 class="item-view-block-title">Facilities</h4>(.*?)</ul>',
    re.DOTALL,
)
_HEAT_WORDS = frozenset({"Heating", "Gas"})
_ORIGIN = "https://myrealty.am"


class MyrealtyAdapter:
    """2-room Yerevan apartments listed on myrealty.am."""

    name = "myrealty"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        site = require_listing(ctx)
        if not site.url:
            raise ValueError("myrealty requires a search url")
        urls = _listing_urls(ctx.fetcher.fetch_text(site.url))
        candidates = []
        for url in urls:
            candidate = _candidate(ctx, url, ctx.fetcher.fetch_text(url))
            if candidate is not None:
                candidates.append(candidate)
        if urls and not candidates:
            raise ValueError("myrealty found no listing fields")
        return AdapterOutput(candidates=candidates)


def _listing_urls(html: str) -> list[str]:
    found: list[str] = []
    for href in _LISTING_HREF.findall(html):
        if href.startswith("/"):
            href = _ORIGIN + href
        href = href.replace(f"{_ORIGIN}/hy/", f"{_ORIGIN}/en/", 1)
        if href not in found:
            found.append(href)
    return found


def _candidate(ctx: CollectContext, url: str, html: str) -> BuildingCandidate | None:
    building_type = _span(html, "Building type")
    area_text = _span(html, "Area")
    if building_type is None and area_text is None:
        return None
    parts = [unquote(part.replace("+", " ")) for part in urlparse(url).path.split("/") if part]
    listing_id = parts[-1]
    address = ", ".join(parts[-4:-1]) if len(parts) >= 4 else None
    coords = _COORDS.search(html)
    edited = _EDITED.search(html)
    seen = (
        date(int(edited.group(3)), int(edited.group(2)), int(edited.group(1)))
        if edited
        else ctx.period.first_day
    )
    heat = [name for name in _facility_names(html) if name in _HEAT_WORDS]
    return BuildingCandidate(
        id=f"myrealty-{listing_id}",
        city=ctx.city.id,
        address=address or None,
        latitude=float(coords.group(1)) if coords else None,
        longitude=float(coords.group(2)) if coords else None,
        heating_raw=" | ".join(heat) or None,
        building_type=building_type,
        floors=_storeys(_span(html, "Floor/Storeys")),
        area_m2=_number(area_text),
        url=url,
        seen_at=seen,
    )


def _span(html: str, label: str) -> str | None:
    found = re.search(
        rf"<label>\s*{re.escape(label)}\s*</label>.*?<span>\s*([^<]+?)\s*</span>",
        html,
        re.DOTALL,
    )
    if found is None:
        return None
    return found.group(1).strip() or None


def _facility_names(html: str) -> list[str]:
    block = _FACILITIES.search(html)
    if block is None:
        return []
    return [name.strip() for name in re.findall(r"<label>([^<]+)</label>", block.group(1))]


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
