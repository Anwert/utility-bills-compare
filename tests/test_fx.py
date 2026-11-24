"""Exchange-rate adapters on saved October 2026 responses. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from ubc.adapter import CollectContext, get_adapter
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import (
    Category,
    Currency,
    ExtractionMethod,
    Item,
    Reliability,
    SourceConfig,
    SourceKind,
    YearMonth,
    ordered_enabled_sources,
)

_PERIOD = YearMonth.parse("2026-10")


def _collect(city_id: str, source: SourceConfig, fixture_dir: str):
    return get_adapter(source.adapter).collect(
        CollectContext(
            city=load_city(city_id),
            period=_PERIOD,
            fetcher=FixtureFetcher(REPO_ROOT / "tests" / "fixtures" / fixture_dir),
            source=source,
        )
    )


def test_ecb_pln_monthly_average_for_october_2026() -> None:
    output = _collect(
        "warsaw",
        SourceConfig(
            id="fx_pln_ecb",
            adapter="fx_ecb",
            kind=SourceKind.official,
            reliability=Reliability.official,
            settings={
                "url": "https://data-api.ecb.europa.eu/service/data/EXR/M.PLN.EUR.SP00.A",
                "currency": "PLN",
            },
        ),
        "fx_ecb",
    )

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city == "all"
    assert record.item is Item.fx
    assert record.category is Category.context
    assert record.currency is Currency.PLN
    assert record.unit == "PLN/EUR"
    assert record.value == pytest.approx(4.381268181818182, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.method is ExtractionMethod.api
    assert record.reliability is Reliability.official
    assert record.source.kind is SourceKind.official
    assert record.source.url == (
        "https://data-api.ecb.europa.eu/service/data/EXR/M.PLN.EUR.SP00.A"
        "?startPeriod=2026-10&endPeriod=2026-10&format=csvdata"
    )
    assert record.note == "ECB monthly average"
    assert record.source.fetched_at.tzinfo is not None


def test_cba_amd_averages_october_2026_daily_rates() -> None:
    fetcher = _SoapCapture(REPO_ROOT / "tests" / "fixtures" / "fx_cba")
    output = get_adapter("fx_cba").collect(
        CollectContext(
            city=load_city("yerevan"),
            period=_PERIOD,
            fetcher=fetcher,
            source=SourceConfig(
                id="fx_amd_cba",
                adapter="fx_cba",
                kind=SourceKind.official,
                reliability=Reliability.official,
                settings={
                    "url": "https://api.cba.am/exchangerates.asmx",
                    "currency": "AMD",
                },
            ),
        )
    )

    assert "DateFrom=2026-10-01" in fetcher.url
    assert "DateTo=2026-10-31" in fetcher.url
    assert "2026-10-01" in fetcher.body and "2026-10-31" in fetcher.body
    assert "EUR" in fetcher.body
    assert fetcher.headers.get("SOAPAction") == "http://www.cba.am/ExchangeRatesByDateRangeByISO"

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city == "all"
    assert record.item is Item.fx
    assert record.category is Category.context
    assert record.currency is Currency.AMD
    assert record.unit == "AMD/EUR"
    assert record.value == pytest.approx(405.045, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.method is ExtractionMethod.api
    assert record.reliability is Reliability.official
    assert record.note == "CBA mean of 22 daily rates"
    assert record.source.url == "https://api.cba.am/exchangerates.asmx"


def test_nbg_gel_averages_every_day_of_october_2026() -> None:
    output = _collect(
        "tbilisi",
        SourceConfig(
            id="fx_gel_nbg",
            adapter="fx_nbg",
            kind=SourceKind.official,
            reliability=Reliability.official,
            settings={
                "url": "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/",
                "currency": "GEL",
            },
        ),
        "fx_nbg",
    )

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city == "all"
    assert record.item is Item.fx
    assert record.category is Category.context
    assert record.currency is Currency.GEL
    assert record.unit == "GEL/EUR"
    assert record.value == pytest.approx(2.9155, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.method is ExtractionMethod.api
    assert record.reliability is Reliability.official
    assert record.note == "NBG mean of 31 days"
    assert record.source.url == (
        "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/?currencies=EUR&date="
    )


def test_eurostat_rsd_monthly_average_for_october_2026() -> None:
    output = _collect(
        "belgrade",
        SourceConfig(
            id="fx_rsd_eurostat",
            adapter="fx_eurostat",
            kind=SourceKind.official,
            priority=0,
            reliability=Reliability.official,
            settings={
                "url": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/ert_bil_eur_m",
                "currency": "RSD",
            },
        ),
        "fx_eurostat",
    )

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city == "all"
    assert record.item is Item.fx
    assert record.category is Category.context
    assert record.currency is Currency.RSD
    assert record.unit == "RSD/EUR"
    assert record.value == pytest.approx(117.4463, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.method is ExtractionMethod.api
    assert record.reliability is Reliability.official
    assert record.note == "Eurostat monthly average (source NBS)"
    assert record.source.url == (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
        "ert_bil_eur_m?currency=RSD&statinfo=AVG&time=2026-10"
    )


def test_cities_configure_one_official_fx_source_and_the_rsd_fallback() -> None:
    warsaw = ordered_enabled_sources(load_city("warsaw").fx_sources)
    yerevan = ordered_enabled_sources(load_city("yerevan").fx_sources)
    tbilisi = ordered_enabled_sources(load_city("tbilisi").fx_sources)
    belgrade = ordered_enabled_sources(load_city("belgrade").fx_sources)

    assert [source.adapter for source in warsaw] == ["fx_ecb"]
    assert [source.adapter for source in yerevan] == ["fx_cba"]
    assert [source.adapter for source in tbilisi] == ["fx_nbg"]
    assert [source.adapter for source in belgrade] == ["fx_eurostat", "fx_nbs"]
    assert belgrade[0].priority < belgrade[1].priority
    assert belgrade[0].id == "fx_rsd_eurostat"
    assert belgrade[1].id == "fx_rsd_nbs"
    for source in (*warsaw, *yerevan, *tbilisi, *belgrade):
        assert source.enabled is True
        assert source.kind is SourceKind.official
        assert source.reliability is Reliability.official
    assert load_city("lisbon").fx_sources == []
    assert load_city("berlin").fx_sources == []


def test_nbs_current_middle_rate_is_the_rsd_fallback() -> None:
    output = _collect(
        "belgrade",
        SourceConfig(
            id="fx_rsd_nbs",
            adapter="fx_nbs",
            kind=SourceKind.official,
            priority=1,
            reliability=Reliability.official,
            settings={
                "url": "https://webappcenter.nbs.rs/ExchangeRateWebApp/ExchangeRate/CurrentMiddleRate",
                "currency": "RSD",
            },
        ),
        "fx_nbs",
    )

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city == "all"
    assert record.item is Item.fx
    assert record.category is Category.context
    assert record.currency is Currency.RSD
    assert record.unit == "RSD/EUR"
    assert record.value == pytest.approx(117.6503, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-11-24"
    assert record.method is ExtractionMethod.regex
    assert record.reliability is Reliability.official
    assert record.note == "NBS current middle rate (cross-check)"
    assert (
        record.source.url
        == "https://webappcenter.nbs.rs/ExchangeRateWebApp/ExchangeRate/CurrentMiddleRate"
    )


class _SoapCapture(FixtureFetcher):
    """Reads the saved SOAP response and keeps the request the adapter sent."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.url = ""
        self.body = ""
        self.headers: dict[str, str] = {}

    def post_text(
        self,
        url: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        self.url = url
        self.body = body
        self.headers = dict(headers or {})
        return super().post_text(url, body, headers=headers)
