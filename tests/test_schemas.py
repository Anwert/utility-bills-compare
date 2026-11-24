"""Normalized records, building candidates, and source order."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ubc.schemas import (
    HDD_UNIT,
    NormalizedRecord,
    SourceConfig,
    SourceKind,
    ordered_enabled_sources,
    sort_sources,
)


def record(**overrides: object) -> dict:
    data: dict = {
        "city": "yerevan",
        "item": "waste",
        "category": "utility",
        "value": 13,
        "unit": "AMD/m2/month",
        "currency": "AMD",
        "valid_from": "2026-01-01",
        "source": {
            "kind": "official",
            "url": "https://example.test/waste",
            "fetched_at": "2026-11-24T00:00:00+00:00",
            "name": "arlis",
        },
        "method": "regex",
        "reliability": "official",
    }
    data.update(overrides)
    return data


def source(source_id: str, kind: str, *, priority: int = 0, enabled: bool = True) -> SourceConfig:
    reliability = {
        "proxy_direct": "official",
        "official": "official",
        "third_party": "third_party",
        "statistics": "statistics",
    }[kind]
    return SourceConfig(
        id=source_id,
        adapter="example_html",
        kind=kind,
        priority=priority,
        enabled=enabled,
        reliability=reliability,
    )


def test_valid_record_accepts_a_month_as_the_start_date() -> None:
    parsed = NormalizedRecord.model_validate(record(valid_from="2026-10"))

    assert parsed.city.value == "yerevan"
    assert parsed.valid_from.isoformat() == "2026-10-01"
    assert parsed.category.value == "utility"
    assert parsed.value == 13


def test_stepped_tariff_accepts_an_open_last_tier() -> None:
    parsed = NormalizedRecord.model_validate(
        record(
            city="tbilisi",
            item="electricity",
            currency="GEL",
            unit="tetri/kWh",
            value=6.267,
            tiers=[
                {"up_to": 101, "value": 6.267},
                {"up_to": 301, "value": 9.667},
                {"up_to": None, "value": 13.467},
            ],
        )
    )

    assert [tier.up_to for tier in parsed.tiers] == [101, 301, None]


@pytest.mark.parametrize(
    "overrides",
    [
        {"city": "nope"},
        {"item": "rent"},
        {"category": "housing"},
        {"unit": ""},
        {"value": -1},
        {"value": True},
        {"reliability": "guess"},
        {"method": "hand"},
        {"extra": 1},
        {"currency": None},
        {"valid_to": "2025-12-31"},
        {"source": {"kind": "official", "url": "not-a-url", "fetched_at": "2026-11-24"}},
        {
            "tiers": [
                {"up_to": 300, "value": 1},
                {"up_to": 100, "value": 2},
            ]
        },
        {
            "tiers": [
                {"up_to": None, "value": 1},
                {"up_to": 100, "value": 2},
            ]
        },
    ],
)
def test_invalid_record_is_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        NormalizedRecord.model_validate(record(**overrides))


def test_reliability_must_match_source_kind() -> None:
    with pytest.raises(ValidationError):
        NormalizedRecord.model_validate(record(reliability="third_party"))


def test_exchange_rate_record_is_shared_across_cities() -> None:
    parsed = NormalizedRecord.model_validate(
        record(
            city="all",
            item="fx",
            category="context",
            value=405.045,
            unit="AMD/EUR",
            currency="AMD",
            valid_from="2026-10",
            source={
                "kind": "official",
                "url": "https://example.test/fx",
                "fetched_at": "2026-11-24T00:00:00Z",
            },
        )
    )

    assert parsed.city == "all"
    assert parsed.valid_from.isoformat() == "2026-10-01"


def test_exchange_rate_cannot_pretend_to_be_one_city() -> None:
    with pytest.raises(ValidationError):
        NormalizedRecord.model_validate(
            record(city="yerevan", item="fx", category="context", unit="AMD/EUR")
        )


def test_degree_day_unit_is_stable() -> None:
    assert HDD_UNIT == "K*day"


def test_degree_days_name_a_city_and_need_no_currency() -> None:
    data = record(
        city="lisbon",
        item="hdd",
        category="context",
        value=0,
        unit="K*day",
        valid_from="2026-10",
    )
    del data["currency"]

    parsed = NormalizedRecord.model_validate(data)

    assert parsed.currency is None
    assert parsed.value == 0


def test_degree_days_are_not_a_global_record() -> None:
    with pytest.raises(ValidationError):
        NormalizedRecord.model_validate(
            record(city="all", item="hdd", category="context", unit="K*day", currency=None)
        )


def test_bill_record_cannot_use_the_all_city() -> None:
    with pytest.raises(ValidationError):
        NormalizedRecord.model_validate(record(city="all"))


def test_sources_are_ordered_by_kind_then_priority_then_file_order() -> None:
    sources = [
        source("stats", "statistics"),
        source("official_later", "official", priority=5),
        source("official_sooner", "official", priority=1),
        source("proxy", "proxy_direct"),
        source("aggregator", "third_party", enabled=False),
        source("official_b", "official"),
        source("official_a", "official"),
    ]

    ordered = [item.id for item in sort_sources(sources)]
    enabled = [item.id for item in ordered_enabled_sources(sources)]

    assert ordered == [
        "proxy",
        "official_b",
        "official_a",
        "official_sooner",
        "official_later",
        "aggregator",
        "stats",
    ]
    assert enabled == [
        "proxy",
        "official_b",
        "official_a",
        "official_sooner",
        "official_later",
        "stats",
    ]
    assert SourceKind.proxy_direct.value == "proxy_direct"
