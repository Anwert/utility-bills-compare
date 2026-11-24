"""Local entry point for the data files.

This command validates the contracts and lists the sources a later pipeline
will call. It does not fetch pages, calculate cells, or publish the site.
Those steps are separate tickets; they should call ``load_cities`` and
``get_adapter`` rather than grow a second loader.

    python -m ubc.pipeline --check
    python -m ubc.pipeline
    python -m ubc.pipeline --city yerevan
"""

from __future__ import annotations

import argparse
import sys

from ubc.adapter import load_adapters
from ubc.load import ConfigError, load_cities, load_example_city_month, load_reference
from ubc.schemas import CityId, ordered_enabled_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate city files, the reference apartment, and the example snapshot.",
    )
    parser.add_argument("--city", choices=[city.value for city in CityId])
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
        print(
            f"ok: {len(cities)} cities, reference apartment "
            f"{apartment.rooms} rooms / {apartment.area_m2:.0f} m2 / "
            f"{apartment.occupants} occupants, "
            f"example snapshot {example.city.value} {example.month}"
        )
        return 0
    if args.city:
        cities = [city for city in cities if city.id.value == args.city]
    for city in cities:
        enabled = []
        for article in city.articles:
            enabled.extend(ordered_enabled_sources(article.sources))
        enabled.extend(ordered_enabled_sources(city.fx_sources))
        enabled.extend(ordered_enabled_sources(city.hdd_sources))
        listings = [site for site in city.listing_sites if site.enabled]
        print(
            f"{city.id.value}: {len(city.articles)} articles, "
            f"{len(enabled)} enabled sources, {len(listings)} listing sites"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
