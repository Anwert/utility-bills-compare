"""A proxy_direct source uses a substituted transport. The default stub opens no socket."""

from __future__ import annotations

import pytest

from ubc.adapter import CollectContext, get_adapter
from ubc.fetch import FetchError, FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.proxy import ProxyEndpoint, fetcher_for_source
from ubc.schemas import Reliability, SourceConfig, SourceKind, YearMonth
from ubc.secrets import SecretConfig, SourceUnavailable

_TARIFF = REPO_ROOT / "tests" / "fixtures" / "example" / "tariff.html"
_URL = "https://example.test/tariff.html"


def _proxy_source(*, country: str | None) -> SourceConfig:
    settings: dict[str, str] = {"url": _URL}
    if country is not None:
        settings["country"] = country
    return SourceConfig(
        id="yerevan_waste_proxy",
        adapter="example_html",
        kind=SourceKind.proxy_direct,
        enabled=True,
        reliability=Reliability.official,
        settings=settings,
    )


def _secrets() -> SecretConfig:
    return SecretConfig.from_env(
        {
            "PROXY_HOST": "proxy.example:8000",
            "PROXY_LOGIN": "resident",
            "PROXY_PASSWORD": "s3cret",
            "PROXY_COUNTRY": "de",
        }
    )


def test_proxy_source_reads_through_the_configured_transport(tmp_path) -> None:
    seen: dict[str, object] = {}

    class Transport:
        def fetch_bytes(self, url: str, *, proxy: ProxyEndpoint, user_agent: str) -> bytes:
            seen["url"] = url
            seen["host"] = proxy.host
            seen["login"] = proxy.login
            seen["password"] = proxy.password
            seen["country"] = proxy.country
            seen["user_agent"] = user_agent
            return _TARIFF.read_bytes()

    fetcher = fetcher_for_source(
        _proxy_source(country="am"),
        FixtureFetcher(tmp_path),
        _secrets(),
        Transport(),
    )
    output = get_adapter("example_html").collect(
        CollectContext(
            city=load_city("yerevan"),
            period=YearMonth.parse("2026-10"),
            fetcher=fetcher,
            source=_proxy_source(country="am"),
        )
    )

    assert output.records[0].value == 13
    assert output.records[0].unit == "AMD/m2/month"
    assert seen["url"] == _URL
    assert seen["host"] == "proxy.example:8000"
    assert seen["login"] == "resident"
    assert seen["password"] == "s3cret"
    assert seen["country"] == "am"
    assert "utility-bills-compare" in str(seen["user_agent"])


def test_proxy_country_falls_back_to_the_secret(tmp_path) -> None:
    seen: dict[str, str] = {}

    class Transport:
        def fetch_bytes(self, url: str, *, proxy: ProxyEndpoint, user_agent: str) -> bytes:
            seen["country"] = proxy.country
            return b"ok"

    fetcher = fetcher_for_source(
        _proxy_source(country=None),
        FixtureFetcher(tmp_path),
        _secrets(),
        Transport(),
    )

    assert fetcher.fetch_bytes(_URL) == b"ok"
    assert seen["country"] == "de"


def test_proxy_without_secrets_is_unavailable(tmp_path) -> None:
    with pytest.raises(SourceUnavailable):
        fetcher_for_source(
            _proxy_source(country="am"),
            FixtureFetcher(tmp_path),
            SecretConfig.from_env({}),
            None,
        )


def test_default_proxy_transport_does_not_open_a_connection(tmp_path) -> None:
    fetcher = fetcher_for_source(
        _proxy_source(country="am"),
        FixtureFetcher(tmp_path),
        _secrets(),
        None,
    )

    with pytest.raises(FetchError, match="disabled"):
        fetcher.fetch_text(_URL)


def test_an_ordinary_source_keeps_the_direct_fetcher(tmp_path) -> None:
    direct = FixtureFetcher(tmp_path)
    source = SourceConfig(
        id="regulator_page",
        adapter="example_html",
        kind=SourceKind.official,
        reliability=Reliability.official,
        settings={"url": _URL},
    )

    assert fetcher_for_source(source, direct, SecretConfig.from_env({}), None) is direct
