"""Dram and rouble rates the site converts euros with. No network."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ubc.display_rates import (
    FILE_NAME,
    DisplayRate,
    DisplayRates,
    collect_display_rate,
    load_display_rates,
)
from ubc.fetch import ContentFetcher, FetchError, FixtureFetcher
from ubc.load import ConfigError, load_city, load_reference
from ubc.monthly import MonthOptions, run_month
from ubc.paths import REPO_ROOT, data_dir
from ubc.schemas import YearMonth

_PERIOD = YearMonth.parse("2026-10")


def _rate(month: str, amd: float = 400.0, rub: float = 90.0) -> DisplayRate:
    return DisplayRate(
        month=month,
        amd_per_eur=amd,
        rub_per_eur=rub,
        source_url="https://www.cba.am/",
        source_name="Центральный банк Армении",
    )


def test_october_2026_rates_come_from_one_saved_answer() -> None:
    rate = collect_display_rate(
        _PERIOD, FixtureFetcher(REPO_ROOT / "tests" / "fixtures" / "fx_cba")
    )

    assert rate.month == "2026-10"
    # The same average the bill of Yerevan is converted with.
    assert rate.amd_per_eur == pytest.approx(405.045, abs=5e-5)
    # 22 banking days, the euro in drams divided by the rouble in drams.
    assert rate.rub_per_eur == pytest.approx(92.8591, abs=5e-4)


class _Answer:
    def __init__(self, body: str) -> None:
        self._body = body

    def post_text(self, url: str, body: str, *, headers: dict[str, str] | None = None) -> str:
        del url, body, headers
        return self._body


def _row(code: str, day: int, rate: str) -> str:
    return (
        f"<ExchangeRatesByRange><Rate>{rate}</Rate><Amount>1</Amount><ISO>{code}</ISO>"
        f"<RateDate>2026-10-0{day}T00:00:00+04:00</RateDate></ExchangeRatesByRange>"
    )


@pytest.mark.parametrize(
    "rows",
    [
        _row("EUR", 1, "400"),
        _row("EUR", 1, "400") + _row("RUB", 2, "4"),
        _row("EUR", 1, "400") + _row("RUB", 1, "0"),
    ],
    ids=["no rouble", "other day", "zero"],
)
def test_an_incomplete_answer_gives_no_rate(rows: str) -> None:
    with pytest.raises(ValueError):
        collect_display_rate(_PERIOD, _Answer(f"<r>{rows}</r>"))  # type: ignore[arg-type]


def test_a_month_is_added_in_order_and_replaced() -> None:
    rates = DisplayRates().with_rate(_rate("2026-10")).with_rate(_rate("2026-09"))
    rates = rates.with_rate(_rate("2026-10", rub=95.0))

    assert [(one.month, one.rub_per_eur) for one in rates.rates] == [
        ("2026-09", 90.0),
        ("2026-10", 95.0),
    ]


def test_a_broken_file_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / FILE_NAME).write_text('{"rates": [{"month": "2026-10"}]}', encoding="utf-8")

    with pytest.raises(ConfigError, match=FILE_NAME):
        load_display_rates(tmp_path)


def _options(data_root: Path, display_rate) -> MonthOptions:
    return MonthOptions(
        period=_PERIOD,
        cities=[load_city("lisbon")],
        reference=load_reference(),
        fetcher=FixtureFetcher(REPO_ROOT / "tests" / "fixtures" / "fx_cba"),
        data_root=data_root,
        display_rate=display_rate,
    )


def test_the_monthly_run_keeps_the_rates_of_other_months(tmp_path: Path) -> None:
    (tmp_path / FILE_NAME).write_text(
        DisplayRates().with_rate(_rate("2026-09")).model_dump_json(), encoding="utf-8"
    )
    report = run_month(_options(tmp_path, collect_display_rate))

    assert FILE_NAME in report.written
    assert [one.month for one in load_display_rates(tmp_path).rates] == ["2026-09", "2026-10"]


def test_without_rates_the_month_is_still_published_with_a_warning(tmp_path: Path) -> None:
    def fail(period: YearMonth, fetcher: ContentFetcher) -> DisplayRate:
        raise FetchError("https://api.cba.am/", "нет ответа")

    (tmp_path / FILE_NAME).write_text(
        DisplayRates().with_rate(_rate("2026-10")).model_dump_json(), encoding="utf-8"
    )
    report = run_month(_options(tmp_path, fail))

    assert "lisbon-2026-10.json" in report.written
    assert FILE_NAME not in report.written
    assert any("курсы" in line and "нет ответа" in line for line in report.warnings)
    assert load_display_rates(tmp_path).rates == [_rate("2026-10")]


def test_a_dry_run_writes_no_rates(tmp_path: Path) -> None:
    run_month(replace(_options(tmp_path, collect_display_rate), dry_run=True))

    assert not (tmp_path / FILE_NAME).exists()


def test_every_published_yerevan_month_has_rates_equal_to_its_bill() -> None:
    rates = {one.month: one for one in load_display_rates(data_dir()).rates}
    months = sorted(data_dir().glob("yerevan-*.json"))
    assert months
    for path in months:
        month = json.loads(path.read_text(encoding="utf-8"))
        bill = month["exchange_rate"]["per_eur"]
        assert rates[month["month"]].amd_per_eur == pytest.approx(bill, rel=1e-6)
