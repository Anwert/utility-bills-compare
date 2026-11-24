"""What the listing adapters share: a polite fetch and a loop that survives bad ads.

``ListingSite.settings`` of these adapters: ``max_ads`` (how many ads one
collection opens, 40 by default) and ``pause_seconds`` (wait between two
requests to the site, 1 by default).
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable, Iterable

from ubc.adapter import AdapterOutput, CollectContext, require_listing
from ubc.fetch import FetchError
from ubc.schemas import BuildingCandidate

Sleep = Callable[[float], None]
ParseAd = Callable[[CollectContext, str, str], BuildingCandidate | None]

_MAX_ADS = 40
_PAUSE_SECONDS = 1.0
# Near the 60 m2 reference flat. Other sizes stay, after these.
_NEAR_REFERENCE_M2 = (45, 80)


class SitePages:
    """Pages of one site, with a pause before every request but the first."""

    def __init__(self, ctx: CollectContext, sleep: Sleep | None) -> None:
        self._fetcher = ctx.fetcher
        self._pause = float(require_listing(ctx).settings.get("pause_seconds", _PAUSE_SECONDS))
        self._sleep = sleep or time.sleep
        self._started = False

    def text(self, url: str) -> str:
        if self._started:
            self._sleep(self._pause)
        self._started = True
        return self._fetcher.fetch_text(url)


def search_url(ctx: CollectContext) -> str:
    site = require_listing(ctx)
    if not site.url:
        raise ValueError(f"{site.adapter} requires a search url")
    return site.url


def max_ads(ctx: CollectContext) -> int:
    return int(require_listing(ctx).settings.get("max_ads", _MAX_ADS))


def collect_ads(
    ctx: CollectContext, pages: SitePages, urls: Iterable[str], parse: ParseAd
) -> AdapterOutput:
    """Read up to ``max_ads`` ads. One unreadable ad is skipped; none readable is a failure."""

    candidates = []
    for url in list(urls)[: max_ads(ctx)]:
        try:
            candidate = parse(ctx, url, pages.text(url))
        except (FetchError, ValueError):
            continue
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f"{require_listing(ctx).adapter} found no listing")
    candidates.sort(key=_far_from_reference)
    return AdapterOutput(candidates=candidates)


def round_robin(groups: Iterable[list[str]]) -> list[str]:
    """First of every group, then the second of every group, and so on."""

    queues = [list(group) for group in groups]
    order: list[str] = []
    while any(queues):
        order.extend(queue.pop(0) for queue in queues if queue)
    return order


def readable(text: str) -> str:
    """One kind of spaces and letters: the sites write "M․" with a one-dot leader."""

    return " ".join(unicodedata.normalize("NFKC", text).split())


def _far_from_reference(candidate: BuildingCandidate) -> bool:
    low, high = _NEAR_REFERENCE_M2
    return candidate.area_m2 is None or not low <= candidate.area_m2 <= high
