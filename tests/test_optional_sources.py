"""Proxy and LLM sources stay out of the policy walk when their secrets are absent."""

from __future__ import annotations

from ubc.schemas import Reliability, SourceConfig, SourceKind
from ubc.secrets import LLM_ADAPTER_NAME, SecretConfig, eligible_sources


def _source(
    source_id: str,
    *,
    kind: SourceKind,
    adapter: str,
    priority: int = 0,
    enabled: bool = True,
) -> SourceConfig:
    reliability = {
        SourceKind.proxy_direct: Reliability.official,
        SourceKind.official: Reliability.official,
        SourceKind.third_party: Reliability.third_party,
        SourceKind.statistics: Reliability.statistics,
    }[kind]
    return SourceConfig(
        id=source_id,
        adapter=adapter,
        kind=kind,
        priority=priority,
        enabled=enabled,
        reliability=reliability,
    )


def test_policy_skips_proxy_and_llm_when_secrets_are_absent() -> None:
    sources = [
        _source("via_proxy", kind=SourceKind.proxy_direct, adapter="example_html"),
        _source("via_llm", kind=SourceKind.official, adapter=LLM_ADAPTER_NAME),
        _source("regulator_page", kind=SourceKind.official, adapter="example_html", priority=1),
        _source("eurostat", kind=SourceKind.statistics, adapter="example_html"),
        _source("turned_off", kind=SourceKind.official, adapter="example_html", enabled=False),
    ]

    called = eligible_sources(sources, SecretConfig.from_env({}))

    assert [source.id for source in called] == ["regulator_page", "eurostat"]


def test_complete_secrets_keep_proxy_first_and_the_llm_source() -> None:
    sources = [
        _source("via_proxy", kind=SourceKind.proxy_direct, adapter="example_html"),
        _source("via_llm", kind=SourceKind.official, adapter=LLM_ADAPTER_NAME),
        _source("regulator_page", kind=SourceKind.official, adapter="example_html", priority=1),
    ]
    secrets = SecretConfig.from_env(
        {
            "PROXY_HOST": "proxy.example:8000",
            "PROXY_LOGIN": "resident",
            "PROXY_PASSWORD": "s3cret",
            "PROXY_COUNTRY": "am",
            "LLM_API_KEY": "test-key",
        }
    )

    called = eligible_sources(sources, secrets)

    assert [source.id for source in called] == ["via_proxy", "via_llm", "regulator_page"]


def test_a_blank_proxy_password_still_skips_the_proxy_source() -> None:
    sources = [
        _source("via_proxy", kind=SourceKind.proxy_direct, adapter="example_html"),
        _source("regulator_page", kind=SourceKind.official, adapter="example_html"),
    ]
    secrets = SecretConfig.from_env(
        {
            "PROXY_HOST": "proxy.example:8000",
            "PROXY_LOGIN": "resident",
            "PROXY_PASSWORD": "   ",
            "PROXY_COUNTRY": "am",
        }
    )

    called = eligible_sources(sources, secrets)

    assert [source.id for source in called] == ["regulator_page"]


def test_repr_does_not_include_the_proxy_password_or_the_llm_key() -> None:
    secrets = SecretConfig.from_env(
        {
            "PROXY_HOST": "proxy.example:8000",
            "PROXY_LOGIN": "resident",
            "PROXY_PASSWORD": "s3cret",
            "PROXY_COUNTRY": "am",
            "LLM_API_KEY": "test-key",
        }
    )

    text = repr(secrets)

    assert "s3cret" not in text
    assert "test-key" not in text
