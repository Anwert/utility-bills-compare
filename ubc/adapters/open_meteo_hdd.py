"""Heating degree-days from the Open-Meteo ERA5 archive.

Eurostat's rule: a day whose mean temperature is at or below 15 °C contributes
``18 - T`` kelvin-days, and a warmer day contributes nothing. ``heating_degree_days``
is that sum for whatever days it is given. ``annual_heating_degree_days`` is the
same sum for one heating year, the denominator a later calculation uses when it
spreads an annual consumption norm across months.

The heating year is the twelve months that end on the last day of the collected
month (October 2026 → 2025-11-01 .. 2026-10-31). The window is fixed by that
month, so it does not depend on the clock, and every day is already in the
archive once the month itself is. The archive runs a few days behind: a month
with days still missing fails the source instead of looking warmer than it was.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from math import isfinite

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.fetch import FetchError
from ubc.schemas import (
    HDD_UNIT,
    ITEM_CATEGORY,
    ExtractionMethod,
    Item,
    NormalizedRecord,
    SourceConfig,
    SourceInfo,
    YearMonth,
)

# Eurostat indoor base and heating threshold, °C.
_BASE_C = 18.0
_THRESHOLD_C = 15.0


def heating_year(period: YearMonth) -> tuple[date, date]:
    """Inclusive first and last day of the heating year for ``period``."""

    end = period.last_day
    index = period.year * 12 + (period.month - 1) - 11
    year, month_zero = divmod(index, 12)
    return date(year, month_zero + 1, 1), end


def heating_degree_days(daily_mean_celsius: Iterable[float | None]) -> float:
    """Eurostat HDD over these daily means, in K·day, rounded to 0.1.

    ``None`` is a day the archive did not report and is skipped.
    """

    total = 0.0
    for temperature in daily_mean_celsius:
        if temperature is None:
            continue
        if isinstance(temperature, bool) or not isinstance(temperature, int | float):
            raise ValueError("daily mean temperature must be a number")
        if not isfinite(temperature):
            raise ValueError("daily mean temperature must be finite")
        if temperature <= _THRESHOLD_C:
            total += _BASE_C - temperature
    return round(total, 1)


def annual_heating_degree_days(daily_mean_celsius: Iterable[float | None]) -> float:
    """HDD of one heating year. Pass the daily means from ``heating_year``."""

    return heating_degree_days(daily_mean_celsius)


# The free archive limits requests per minute and address, and a CI runner shares
# its address with strangers. An hourly or daily limit outlasts these waits.
_TOO_MANY_REQUESTS = 429
_WAITS_SECONDS = (65.0, 130.0)

# Without ``models`` the archive answers with its best-match model, not ERA5.
_DAILY = "daily=temperature_2m_mean&timezone=auto&models=era5"


class OpenMeteoHddAdapter:
    """Monthly and heating-year HDD for one city from the Open-Meteo archive.

    Coordinates come from the city file. ``settings.url`` is the archive
    endpoint without a query string. The collected month is ``ctx.period``.
    """

    name = "open_meteo_hdd"

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        endpoint = _endpoint(source)
        fetched_at = datetime.now(UTC)
        month_start = ctx.period.first_day
        month_end = ctx.period.last_day
        year_start, year_end = heating_year(ctx.period)
        records = [
            _record(
                ctx,
                source,
                Item.hdd,
                endpoint,
                month_start,
                month_end,
                fetched_at,
                annual=False,
                sleep=self._sleep,
            ),
            _record(
                ctx,
                source,
                Item.hdd_annual,
                endpoint,
                year_start,
                year_end,
                fetched_at,
                annual=True,
                sleep=self._sleep,
            ),
        ]
        return AdapterOutput(records=records)


def _endpoint(source: SourceConfig) -> str:
    url = source.settings.get("url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise ValueError("open_meteo_hdd requires settings.url")
    return url.split("?", 1)[0].rstrip("/")


def _coord(value: float) -> str:
    return format(value, ".10f").rstrip("0").rstrip(".")


def _archive_url(endpoint: str, latitude: float, longitude: float, start: date, end: date) -> str:
    return (
        f"{endpoint}?latitude={_coord(latitude)}&longitude={_coord(longitude)}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}&{_DAILY}"
    )


def _daily_means(body: str, start: date, end: date) -> list[float | None]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Open-Meteo response is not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Open-Meteo response is not an object")
    if payload.get("error"):
        reason = payload.get("reason") or "request failed"
        raise ValueError(f"Open-Meteo error: {reason}")
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("Open-Meteo response has no daily temperatures")
    times = daily.get("time")
    temperatures = daily.get("temperature_2m_mean")
    if (
        not isinstance(times, list)
        or not isinstance(temperatures, list)
        or len(times) != len(temperatures)
    ):
        raise ValueError("Open-Meteo daily temperatures are missing or uneven")
    selected: list[float | None] = []
    for day, temperature in zip(times, temperatures, strict=True):
        if not isinstance(day, str):
            raise ValueError("Open-Meteo day is not a date")
        try:
            parsed = date.fromisoformat(day)
        except ValueError as exc:
            raise ValueError("Open-Meteo day is not a date") from exc
        if start <= parsed <= end:
            selected.append(temperature)
    return selected


def _ask(ctx: CollectContext, url: str, sleep: Callable[[float], None]) -> str:
    for wait in _WAITS_SECONDS:
        try:
            return ctx.fetcher.fetch_text(url)
        except FetchError as exc:
            if exc.status != _TOO_MANY_REQUESTS:
                raise
        sleep(wait)
    return ctx.fetcher.fetch_text(url)


def _record(
    ctx: CollectContext,
    source: SourceConfig,
    item: Item,
    endpoint: str,
    start: date,
    end: date,
    fetched_at: datetime,
    *,
    annual: bool,
    sleep: Callable[[float], None],
) -> NormalizedRecord:
    url = _archive_url(endpoint, ctx.city.latitude, ctx.city.longitude, start, end)
    temperatures = _daily_means(_ask(ctx, url, sleep), start, end)
    present = sum(temperature is not None for temperature in temperatures)
    if present == 0:
        raise ValueError(f"Open-Meteo returned no daily temperatures for {url}")
    days = (end - start).days + 1
    if not annual and present < days:
        raise ValueError(f"Open-Meteo has {present} of {days} days of the month so far")
    value = (
        annual_heating_degree_days(temperatures) if annual else heating_degree_days(temperatures)
    )
    note = f"{present} days"
    if annual:
        note = f"{present} days, {start.isoformat()}..{end.isoformat()}"
    return NormalizedRecord(
        city=ctx.city.id,
        item=item,
        category=ITEM_CATEGORY[item],
        value=value,
        unit=HDD_UNIT,
        valid_from=ctx.period.first_day,
        valid_to=ctx.period.last_day,
        source=SourceInfo(
            kind=source.kind,
            url=url,
            fetched_at=fetched_at,
            name="Open-Meteo ERA5",
        ),
        method=ExtractionMethod.api,
        reliability=source.reliability,
        note=note,
    )


open_meteo_hdd = register_adapter(OpenMeteoHddAdapter())
