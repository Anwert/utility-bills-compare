"""Exchange-rate adapters on saved October 2026 responses.

Expected rates are the figures checked on 24.11.2026, not values recomputed
from the fixture the way the adapter does. Tolerance is four decimal places.
"""

from __future__ import annotations

import pytest

from ubc.adapter import AdapterOutput, CollectContext, get_adapter
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import (
    ExtractionMethod,
    Reliability,
    SourceConfig,
    SourceKind,
    YearMonth,
    ordered_enabled_sources,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures"
PERIOD = YearMonth.parse("2026-10")


def _collect(city_id: str, source: SourceConfig, fixture: str) -> AdapterOutput:
    return get_adapter(source.adapter).collect(
        CollectContext(
            city=load_city(city_id),
            period=PERIOD,
            fetcher=FixtureFetcher(FIXTURES / fixture),
            source=source,
        )
    )


def _source(source_id: str, adapter: str, url: str, currency: str) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        adapter=adapter,
        kind=SourceKind.official,
        priority=0,
        enabled=True,
        reliability=Reliability.official,
        settings={"url": url, "currency": currency},
    )


def test_ecb_october_2026_pln_average() -> None:
    output = _collect(
        "warsaw",
        _source(
            "fx_pln_ecb",
            "fx_ecb",
            "https://data-api.ecb.europa.eu/service/data/EXR/M.PLN.EUR.SP00.A",
            "PLN",
        ),
        "fx_ecb",
    )

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city == "all"
    assert record.item.value == "fx"
    assert record.category.value == "context"
    assert record.currency is not None and record.currency.value == "PLN"
    assert record.unit == "PLN/EUR"
    assert record.value == pytest.approx(4.381268181818182, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.valid_to is not None and record.valid_to.isoformat() == "2026-10-31"
    assert record.method is ExtractionMethod.api
    assert record.reliability is Reliability.official
    assert record.source.kind is SourceKind.official
    assert record.source.fetched_at.tzinfo is not None
    assert record.source.url == (
        "https://data-api.ecb.europa.eu/service/data/EXR/M.PLN.EUR.SP00.A"
        "?startPeriod=2026-10&endPeriod=2026-10&format=csvdata"
    )
    assert record.note == "ECB monthly average"


def test_cba_october_2026_amd_average() -> None:
    output = _collect(
        "yerevan",
        _source(
            "fx_amd_cba",
            "fx_cba",
            "https://api.cba.am/exchangerates.asmx",
            "AMD",
        ),
        "fx_cba",
    )

    record = output.records[0]
    assert record.city == "all"
    assert record.currency is not None and record.currency.value == "AMD"
    assert record.unit == "AMD/EUR"
    assert record.value == pytest.approx(405.045, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.valid_to is not None and record.valid_to.isoformat() == "2026-10-31"
    assert record.method is ExtractionMethod.api
    assert record.note == "CBA mean of 22 daily rates"
    assert record.source.url == (
        "https://api.cba.am/exchangerates.asmx?DateFrom=2026-10-01&DateTo=2026-10-31&ISOCodes=EUR"
    )


def test_nbg_october_2026_gel_average() -> None:
    output = _collect(
        "tbilisi",
        _source(
            "fx_gel_nbg",
            "fx_nbg",
            "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/",
            "GEL",
        ),
        "fx_nbg",
    )

    record = output.records[0]
    assert record.city == "all"
    assert record.currency is not None and record.currency.value == "GEL"
    assert record.unit == "GEL/EUR"
    assert record.value == pytest.approx(2.9155, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.valid_to is not None and record.valid_to.isoformat() == "2026-10-31"
    assert record.method is ExtractionMethod.api
    assert record.note == "NBG mean of 31 days"
    assert record.source.url == "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/"


def test_eurostat_october_2026_rsd_average() -> None:
    output = _collect(
        "belgrade",
        _source(
            "fx_rsd_eurostat",
            "fx_eurostat",
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/ert_bil_eur_m",
            "RSD",
        ),
        "fx_eurostat",
    )

    record = output.records[0]
    assert record.city == "all"
    assert record.currency is not None and record.currency.value == "RSD"
    assert record.unit == "RSD/EUR"
    assert record.value == pytest.approx(117.4463, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-10-01"
    assert record.valid_to is not None and record.valid_to.isoformat() == "2026-10-31"
    assert record.method is ExtractionMethod.api
    assert record.note == "Eurostat monthly average (source NBS)"
    assert record.source.url == (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
        "ert_bil_eur_m?currency=RSD&statinfo=AVG&time=2026-10"
    )


def test_nbs_page_is_the_current_middle_rate_not_the_month_average() -> None:
    output = _collect(
        "belgrade",
        _source(
            "fx_rsd_nbs",
            "fx_nbs",
            "https://webappcenter.nbs.rs/ExchangeRateWebApp/ExchangeRate/CurrentMiddleRate",
            "RSD",
        ),
        "fx_nbs",
    )

    record = output.records[0]
    assert record.city == "all"
    assert record.currency is not None and record.currency.value == "RSD"
    assert record.unit == "RSD/EUR"
    assert record.value == pytest.approx(117.6503, abs=5e-5)
    assert record.valid_from.isoformat() == "2026-11-24"
    assert record.valid_to is not None and record.valid_to.isoformat() == "2026-11-24"
    assert record.method is ExtractionMethod.regex
    assert record.note == "NBS current middle rate (cross-check)"
    assert (
        record.source.url
        == "https://webappcenter.nbs.rs/ExchangeRateWebApp/ExchangeRate/CurrentMiddleRate"
    )


def test_rsd_fallback_is_configured_after_the_monthly_average() -> None:
    sources = ordered_enabled_sources(load_city("belgrade").fx_sources)

    assert [(source.adapter, source.priority) for source in sources] == [
        ("fx_eurostat", 0),
        ("fx_nbs", 1),
    ]
    assert all(source.kind is SourceKind.official for source in sources)
    assert sources[1].settings["url"] == (
        "https://webappcenter.nbs.rs/ExchangeRateWebApp/ExchangeRate/CurrentMiddleRate"
    )


def test_only_non_euro_cities_list_an_fx_source() -> None:
    assert [source.adapter for source in load_city("warsaw").fx_sources] == ["fx_ecb"]
    assert [source.adapter for source in load_city("yerevan").fx_sources] == ["fx_cba"]
    assert [source.adapter for source in load_city("tbilisi").fx_sources] == ["fx_nbg"]
    assert load_city("lisbon").fx_sources == []
    assert load_city("berlin").fx_sources == []
