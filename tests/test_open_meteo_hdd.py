"""Eurostat heating degree-days from saved Open-Meteo archive JSON.

Expected HDD figures were read off the ERA5 archive on 25.11.2026, apart from
this code. They are literals: the test does not add the temperatures up again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ubc.adapter import AdapterOutput, CollectContext, get_adapter
from ubc.adapters.open_meteo_hdd import (
    OpenMeteoHddAdapter,
    annual_heating_degree_days,
    heating_degree_days,
    heating_year,
)
from ubc.fetch import FetchError, FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import HDD_UNIT, Reliability, SourceConfig, SourceKind, YearMonth

# ERA5 (models=era5). Without the parameter Open-Meteo answers with its best-match model.
OCTOBER_2026_HDD = {
    "yerevan": 46.4,
    "tbilisi": 41.2,
    "belgrade": 126.9,
    "warsaw": 252.3,
    "lisbon": 0,
    "berlin": 219.4,
}

# Heating year 2025-11-01 .. 2026-10-31, read once from the saved archive.
HEATING_YEAR_HDD = {
    "yerevan": 2296.8,
    "tbilisi": 2067.6,
    "belgrade": 2112.8,
    "warsaw": 3347.9,
    "lisbon": 666.5,
    "berlin": 2965.6,
}

_MONTH_URL = {
    "yerevan": "https://archive-api.open-meteo.com/v1/archive?latitude=40.18&longitude=44.51&start_date=2026-10-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "tbilisi": "https://archive-api.open-meteo.com/v1/archive?latitude=41.72&longitude=44.79&start_date=2026-10-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "belgrade": "https://archive-api.open-meteo.com/v1/archive?latitude=44.82&longitude=20.46&start_date=2026-10-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "warsaw": "https://archive-api.open-meteo.com/v1/archive?latitude=52.23&longitude=21.01&start_date=2026-10-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "lisbon": "https://archive-api.open-meteo.com/v1/archive?latitude=38.72&longitude=-9.14&start_date=2026-10-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "berlin": "https://archive-api.open-meteo.com/v1/archive?latitude=52.52&longitude=13.4&start_date=2026-10-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
}

_YEAR_URL = {
    "yerevan": "https://archive-api.open-meteo.com/v1/archive?latitude=40.18&longitude=44.51&start_date=2025-11-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "tbilisi": "https://archive-api.open-meteo.com/v1/archive?latitude=41.72&longitude=44.79&start_date=2025-11-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "belgrade": "https://archive-api.open-meteo.com/v1/archive?latitude=44.82&longitude=20.46&start_date=2025-11-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "warsaw": "https://archive-api.open-meteo.com/v1/archive?latitude=52.23&longitude=21.01&start_date=2025-11-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "lisbon": "https://archive-api.open-meteo.com/v1/archive?latitude=38.72&longitude=-9.14&start_date=2025-11-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
    "berlin": "https://archive-api.open-meteo.com/v1/archive?latitude=52.52&longitude=13.4&start_date=2025-11-01&end_date=2026-10-31&daily=temperature_2m_mean&timezone=auto&models=era5",
}

_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "open_meteo"
_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def test_day_at_15_counts_and_a_warmer_day_does_not() -> None:
    # 15 °C → 18 − 15 = 3; 15.1 °C is above the Eurostat threshold → 0.
    assert heating_degree_days([15, 15.1]) == 3


def test_missing_day_is_skipped() -> None:
    # 10 °C → 8; a missing day adds nothing; 20 °C → 0.
    assert heating_degree_days([10, None, 20]) == 8


def test_sum_is_rounded_to_a_tenth() -> None:
    # 14.26 °C → 18 − 14.26 = 3.74, which rounds to 3.7 K·day.
    assert heating_degree_days([14.26]) == 3.7


def test_annual_heating_degree_days_sums_the_heating_year() -> None:
    # Stand-in year: 0 °C → 18, 15 °C → 3, 16 °C → 0.
    assert annual_heating_degree_days([0, 15, 16]) == 21


def test_heating_year_for_october_2026_is_the_trailing_twelve_months() -> None:
    start, end = heating_year(YearMonth.parse("2026-10"))

    assert start.isoformat() == "2025-11-01"
    assert end.isoformat() == "2026-10-31"


def test_heating_year_for_january_starts_the_previous_february() -> None:
    start, end = heating_year(YearMonth.parse("2026-01"))

    assert start.isoformat() == "2025-02-01"
    assert end.isoformat() == "2026-01-31"


def test_heating_year_for_december_is_that_calendar_year() -> None:
    start, end = heating_year(YearMonth.parse("2026-12"))

    assert start.isoformat() == "2026-01-01"
    assert end.isoformat() == "2026-12-31"


def test_heating_year_ending_in_a_leap_february_includes_the_29th() -> None:
    start, end = heating_year(YearMonth.parse("2024-02"))

    assert start.isoformat() == "2023-03-01"
    assert end.isoformat() == "2024-02-29"


def test_temperature_that_is_not_a_number_is_rejected() -> None:
    with pytest.raises(ValueError):
        heating_degree_days(["15"])  # type: ignore[list-item]


def _context(city_id: str, fetcher: FixtureFetcher | None = None) -> CollectContext:
    city = load_city(city_id)
    return CollectContext(
        city=city,
        period=YearMonth.parse("2026-10"),
        fetcher=fetcher or FixtureFetcher(_FIXTURES),
        source=city.hdd_sources[0],
    )


@pytest.mark.parametrize("city_id", list(OCTOBER_2026_HDD))
def test_october_2026_fixture_returns_monthly_and_annual_hdd(city_id: str) -> None:
    output = get_adapter("open_meteo_hdd").collect(_context(city_id))

    assert isinstance(output, AdapterOutput)
    assert output.candidates == []
    by_item = {record.item.value: record for record in output.records}
    assert set(by_item) == {"hdd", "hdd_annual"}

    month = by_item["hdd"]
    year = by_item["hdd_annual"]
    assert month.value == OCTOBER_2026_HDD[city_id]
    assert year.value == HEATING_YEAR_HDD[city_id]
    for record, url in ((month, _MONTH_URL[city_id]), (year, _YEAR_URL[city_id])):
        assert record.city.value == city_id
        assert record.category.value == "context"
        assert record.unit == HDD_UNIT
        assert record.currency is None
        assert record.tiers == []
        assert record.method.value == "api"
        assert record.reliability is Reliability.third_party
        assert record.source.kind.value == "third_party"
        assert record.source.url == url
        assert record.source.name == "Open-Meteo ERA5"
        assert record.source.fetched_at.tzinfo is not None
        assert record.valid_from.isoformat() == "2026-10-01"
        assert record.valid_to is not None
        assert record.valid_to.isoformat() == "2026-10-31"
    assert month.note == "31 days"
    assert year.note == "365 days, 2025-11-01..2026-10-31"


def test_every_city_lists_open_meteo_for_degree_days() -> None:
    for city_id in OCTOBER_2026_HDD:
        source = load_city(city_id).hdd_sources[0]
        assert source.id == "hdd_open_meteo"
        assert source.adapter == "open_meteo_hdd"
        assert source.enabled is True
        assert source.kind is SourceKind.third_party
        assert source.reliability is Reliability.third_party
        assert source.priority == 0
        assert source.settings["url"] == _ARCHIVE


def test_source_without_archive_url_is_rejected() -> None:
    city = load_city("yerevan")
    context = CollectContext(
        city=city,
        period=YearMonth.parse("2026-10"),
        fetcher=FixtureFetcher(_FIXTURES),
        source=SourceConfig(
            id="hdd_open_meteo",
            adapter="open_meteo_hdd",
            kind=SourceKind.third_party,
            reliability=Reliability.third_party,
            settings={},
        ),
    )

    with pytest.raises(ValueError, match="settings.url"):
        get_adapter("open_meteo_hdd").collect(context)


def test_archive_error_payload_fails_the_source(tmp_path: Path) -> None:
    (tmp_path / "error.json").write_text(
        '{"error": true, "reason": "out of range"}',
        encoding="utf-8",
    )
    fetcher = FixtureFetcher(tmp_path, {_MONTH_URL["yerevan"]: "error.json"})

    with pytest.raises(ValueError, match="out of range"):
        get_adapter("open_meteo_hdd").collect(_context("yerevan", fetcher))


def test_archive_without_temperatures_fails_the_source(tmp_path: Path) -> None:
    month_url = _MONTH_URL["yerevan"]
    (tmp_path / "empty.json").write_text(
        '{"daily": {"time": [], "temperature_2m_mean": []}}',
        encoding="utf-8",
    )
    fetcher = FixtureFetcher(tmp_path, {month_url: "empty.json"})

    with pytest.raises(ValueError, match="temperature"):
        get_adapter("open_meteo_hdd").collect(_context("yerevan", fetcher))


def test_month_the_archive_has_not_finished_fails_the_source(tmp_path: Path) -> None:
    month_url = _MONTH_URL["yerevan"]
    body = json.loads((_FIXTURES / "yerevan-2026-10.json").read_text(encoding="utf-8"))
    body["daily"]["temperature_2m_mean"][-2:] = [None, None]
    (tmp_path / "early.json").write_text(json.dumps(body), encoding="utf-8")
    fetcher = FixtureFetcher(tmp_path, {month_url: "early.json"})

    with pytest.raises(ValueError, match="29 of 31"):
        get_adapter("open_meteo_hdd").collect(_context("yerevan", fetcher))


@pytest.mark.parametrize("keep", [30, 364], ids=["one month of the year", "one day missing"])
@pytest.mark.parametrize("how", ["null", "absent"])
def test_heating_year_with_missing_days_fails_the_source(
    tmp_path: Path, keep: int, how: str
) -> None:
    body = json.loads((_FIXTURES / "yerevan-2025-11_2026-10.json").read_text(encoding="utf-8"))
    daily = body["daily"]
    if how == "null":
        daily["temperature_2m_mean"][keep:] = [None] * (365 - keep)
    else:
        daily["time"], daily["temperature_2m_mean"] = (
            daily["time"][:keep],
            daily["temperature_2m_mean"][:keep],
        )
    (tmp_path / "year.json").write_text(json.dumps(body), encoding="utf-8")
    month = (_FIXTURES / "yerevan-2026-10.json").read_text(encoding="utf-8")
    (tmp_path / "month.json").write_text(month, encoding="utf-8")
    fetcher = FixtureFetcher(
        tmp_path, {_MONTH_URL["yerevan"]: "month.json", _YEAR_URL["yerevan"]: "year.json"}
    )

    with pytest.raises(ValueError, match=f"{keep} of 365"):
        get_adapter("open_meteo_hdd").collect(_context("yerevan", fetcher))


class _Busy(FixtureFetcher):
    """Answers with ``status`` to the first ``refusals`` requests."""

    def __init__(self, refusals: int, status: int) -> None:
        super().__init__(REPO_ROOT / "tests" / "fixtures" / "open_meteo")
        self.refusals = refusals
        self.status = status

    def fetch_text(self, url: str) -> str:
        if self.refusals:
            self.refusals -= 1
            raise FetchError(url, "refused", status=self.status)
        return super().fetch_text(url)


def _busy_yerevan(fetcher: _Busy, waits: list[float]) -> AdapterOutput:
    return OpenMeteoHddAdapter(sleep=waits.append).collect(
        CollectContext(
            city=load_city("yerevan"),
            period=YearMonth.parse("2026-10"),
            fetcher=fetcher,
            source=load_city("yerevan").hdd_sources[0],
        )
    )


def test_too_many_requests_is_asked_again_after_a_minute() -> None:
    waits: list[float] = []

    output = _busy_yerevan(_Busy(refusals=2, status=429), waits)

    assert [record.value for record in output.records] == [46.4, 2296.8]
    assert len(waits) == 2
    assert min(waits) >= 60


@pytest.mark.parametrize(("refusals", "status"), [(9, 429), (1, 500)])
def test_lasting_limit_or_another_error_is_a_failed_source(refusals: int, status: int) -> None:
    with pytest.raises(FetchError):
        _busy_yerevan(_Busy(refusals=refusals, status=status), [])
