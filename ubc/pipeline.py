"""Local entry point for the data files.

This command validates the contracts and lists the sources a later pipeline
will call. It does not fetch pages, calculate cells, or publish the site.
Those steps are separate tickets; they should call ``load_cities``,
``collect_contexts``, and ``get_adapter`` rather than grow a second loader.

    python -m ubc.pipeline --check
    python -m ubc.pipeline
    python -m ubc.pipeline --city yerevan --period 2026-10
"""

from __future__ import annotations

import argparse
import sys

from ubc.adapter import CollectContext, load_adapters
from ubc.fetch import ContentFetcher
from ubc.load import ConfigError, load_cities, load_example_city_month, load_reference
from ubc.schemas import CityConfig, CityId, SourceConfig, YearMonth, ordered_enabled_sources


def collect_contexts(
    city: CityConfig,
    period: YearMonth,
    fetcher: ContentFetcher,
) -> list[CollectContext]:
    """One context per enabled tariff source and enabled listing site.

    The same ``period`` is on every context. Listing contexts carry a
    ``ListingSite``; the others carry a ``SourceConfig``. Nothing is fetched.
    """

    sources: list[SourceConfig] = []
    for article in city.articles:
        sources.extend(ordered_enabled_sources(article.sources))
    sources.extend(ordered_enabled_sources(city.fx_sources))
    sources.extend(ordered_enabled_sources(city.hdd_sources))
    contexts = [
        CollectContext(city=city, period=period, fetcher=fetcher, source=source)
        for source in sources
    ]
    contexts.extend(
        CollectContext(city=city, period=period, fetcher=fetcher, source=site)
        for site in city.listing_sites
        if site.enabled
    )
    return contexts


def _parse_period(value: str) -> YearMonth:
    try:
        return YearMonth.parse(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate city files, the reference apartment, and the example snapshot.",
    )
    parser.add_argument("--city", choices=[city.value for city in CityId])
    parser.add_argument(
        "--period",
        type=_parse_period,
        help="Month to collect, YYYY-MM. Passed through on every CollectContext.",
    )
    args = parser.parse_args(argv)
    try:
        cities = load_cities()
        reference = load_reference()
        example = load_example_city_month()
        load_adapters()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.check:
        apartment = reference.apartment
        period = f", period {args.period}" if args.period is not None else ""
        print(
            f"ok: {len(cities)} cities, reference apartment "
            f"{apartment.rooms} rooms / {apartment.area_m2:.0f} m2 / "
            f"{apartment.occupants} occupants, "
            f"example snapshot {example.city.value} {example.month}{period}"
        )
        return 0
    if args.city:
        cities = [city for city in cities if city.id.value == args.city]
    # A fetcher is part of the context. This command does not call it.
    fetcher = _SilentFetcher()
    for city in cities:
        enabled = []
        for article in city.articles:
            enabled.extend(ordered_enabled_sources(article.sources))
        enabled.extend(ordered_enabled_sources(city.fx_sources))
        enabled.extend(ordered_enabled_sources(city.hdd_sources))
        listings = [site for site in city.listing_sites if site.enabled]
        line = (
            f"{city.id.value}: {len(city.articles)} articles, "
            f"{len(enabled)} enabled sources, {len(listings)} listing sites"
        )
        if args.period is not None:
            contexts = collect_contexts(city, args.period, fetcher)
            line += f", period {args.period}, {len(contexts)} collect calls"
        print(line)
    return 0


class _SilentFetcher:
    """Satisfies CollectContext when the command only counts calls."""

    def fetch_bytes(self, url: str) -> bytes:
        raise RuntimeError(f"pipeline listing does not fetch {url}")

    def fetch_text(self, url: str) -> str:
        raise RuntimeError(f"pipeline listing does not fetch {url}")

    def post_text(
        self,
        url: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        del body, headers
        raise RuntimeError(f"pipeline listing does not fetch {url}")

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        raise RuntimeError(f"pipeline listing does not fetch {url}")

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        raise RuntimeError(f"pipeline listing does not fetch {url}")


if __name__ == "__main__":
    raise SystemExit(main())
