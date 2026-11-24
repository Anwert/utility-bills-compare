"""Command line of the monthly pipeline.

Without ``--run`` the command only validates the data files or lists the sources
of a month: it does not fetch pages, calculate cells, or publish.

    python -m ubc.pipeline --check
    python -m ubc.pipeline
    python -m ubc.pipeline --city yerevan --period 2026-10

``--run`` is the monthly run (``ubc.monthly``): collect, choose, alert, pin
houses, calculate, write ``data/``. The month is the previous calendar month
unless ``--period`` says otherwise. Exit code 1 means nothing was written.

    python -m ubc.pipeline --run
    python -m ubc.pipeline --run --city yerevan --period 2026-10 --dry-run
    python -m ubc.pipeline --run --city yerevan --item gas
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from ubc.adapter import CollectContext, load_adapters
from ubc.alerts import GitHubIssues, GitHubRestClient
from ubc.fetch import ContentFetcher, HttpFetcher
from ubc.load import ConfigError, load_cities, load_example_city_month, load_reference
from ubc.monthly import MonthOptions, MonthReport, PublicationError, run_month
from ubc.paths import data_dir
from ubc.schemas import (
    CHARGE_ITEMS,
    CityConfig,
    CityId,
    Item,
    ReferenceData,
    SourceConfig,
    YearMonth,
    ordered_enabled_sources,
)
from ubc.secrets import SecretConfig

# psrc.am drops about one connection in ten.
_RETRY_PAUSES = (5.0, 15.0)


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


def _previous_month(today: date) -> YearMonth:
    if today.month == 1:
        return YearMonth(today.year - 1, 12)
    return YearMonth(today.year, today.month - 1)


def main(
    argv: list[str] | None = None,
    *,
    today: date | None = None,
    env: Mapping[str, str] | None = None,
    runner: Callable[[MonthOptions], MonthReport] = run_month,
) -> int:
    """Run the command. Tests replace the clock, the environment, and the run itself."""

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
    parser.add_argument(
        "--run",
        action="store_true",
        help="Collect the month, calculate the cells, and write data/.",
    )
    parser.add_argument(
        "--item",
        choices=[item.value for item in CHARGE_ITEMS],
        help="With --run: collect one article, take the others from data/state.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --run: calculate everything, write nothing, send nothing.",
    )
    parser.add_argument(
        "--no-alerts",
        action="store_true",
        help="With --run: do not open or close data-alert issues.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="With --run: the directory to read state from and write to. Default: data/.",
    )
    args = parser.parse_args(argv)
    clock = today or datetime.now(UTC).date()
    if args.run:
        args.period = args.period or _previous_month(clock)
        if args.period.last_day >= clock:
            parser.error(f"--period {args.period} has not ended yet")
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
    if args.run:
        return _run(args, cities, reference, os.environ if env is None else env, runner)
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


def _run(
    args: argparse.Namespace,
    cities: list[CityConfig],
    reference: ReferenceData,
    env: Mapping[str, str],
    runner: Callable[[MonthOptions], MonthReport],
) -> int:
    options = MonthOptions(
        period=args.period,
        cities=cities,
        reference=reference,
        fetcher=HttpFetcher(retry_pauses=_RETRY_PAUSES),
        data_root=args.data_root or data_dir(),
        item=Item(args.item) if args.item else None,
        issues=None if args.no_alerts else _issues(env),
        secrets=SecretConfig.from_env(env),
        dry_run=args.dry_run,
        hide=tuple(filter(None, (_token(env),))),
        log=lambda line: print(line, flush=True),
    )
    try:
        report = runner(options)
    except (PublicationError, ConfigError) as exc:
        print(f"не опубликовано:\n{exc}", file=sys.stderr)
        return 1
    print(f"{args.period}: городов {len(report.months)}, файлов записано {len(report.written)}")
    return 0


def _token(env: Mapping[str, str]) -> str:
    return env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""


def _issues(env: Mapping[str, str]) -> GitHubIssues | None:
    """The repository to alert. A run without its token and name sends no alerts."""

    owner, _, repo = env.get("GITHUB_REPOSITORY", "").partition("/")
    token = _token(env)
    if not (owner and repo and token):
        return None
    return GitHubRestClient(owner, repo, token)


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
