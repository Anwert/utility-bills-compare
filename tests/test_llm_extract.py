"""LLM wrapper on a saved page and a saved model response. No network, no API key."""

from __future__ import annotations

import pytest

from ubc.adapter import CollectContext, get_adapter
from ubc.adapters.llm_extract import ExtractionRejected, LlmExtractAdapter, LlmRequest
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import Reliability, SourceConfig, SourceKind, YearMonth
from ubc.secrets import LLM_ADAPTER_NAME, SourceUnavailable

_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"
_PAGE = "https://regulator.example/yerevan-electricity.txt"


def _context(
    response_name: str | None,
    period: str = "2026-11",
    *,
    mode: str = "text",
    url: str = _PAGE,
) -> CollectContext:
    settings: dict[str, str] = {"url": url, "item": "electricity", "mode": mode}
    if response_name is not None:
        settings["model_response_url"] = f"https://regulator.example/{response_name}"
    return CollectContext(
        city=load_city("yerevan"),
        period=YearMonth.parse(period),
        fetcher=FixtureFetcher(_FIXTURES),
        source=SourceConfig(
            id="yerevan_electricity_llm",
            adapter=LLM_ADAPTER_NAME,
            kind=SourceKind.official,
            enabled=True,
            reliability=Reliability.official,
            settings=settings,
        ),
    )


def test_saved_model_response_becomes_a_normalized_record() -> None:
    output = get_adapter(LLM_ADAPTER_NAME).collect(_context("accepted.json"))

    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city.value == "yerevan"
    assert record.item.value == "electricity"
    assert record.category.value == "utility"
    assert record.value == 46.48
    assert record.unit == "AMD/kWh"
    assert record.currency is not None and record.currency.value == "AMD"
    assert record.valid_from.isoformat() == "2026-01-26"
    assert record.valid_to is None
    assert record.tiers == []
    assert record.method.value == "llm_text"
    assert record.reliability is Reliability.official
    assert record.source.kind is SourceKind.official
    assert record.source.url == _PAGE
    assert record.source.fetched_at.tzinfo is not None
    assert record.note == "46.48 AMD/kWh"


def test_a_unit_outside_the_allowed_list_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("bad_unit.json"))

    assert caught.value.rule == "unit"


def test_a_value_outside_the_city_range_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("out_of_range.json"))

    assert caught.value.rule == "range"


def test_a_quote_missing_from_the_source_text_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("missing_quote.json"))

    assert caught.value.rule == "quote"


def test_a_start_date_more_than_three_months_ahead_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("future_date.json"))

    assert caught.value.rule == "date"


def test_a_start_date_three_calendar_months_after_the_collected_month_is_kept() -> None:
    output = get_adapter(LLM_ADAPTER_NAME).collect(_context("date_on_limit.json", "2026-05"))

    assert output.records[0].valid_from.isoformat() == "2026-08-31"


def test_the_day_after_that_three_month_limit_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("date_past_limit.json", "2026-05"))

    assert caught.value.rule == "date"


def test_the_russian_household_category_is_accepted() -> None:
    record = get_adapter(LLM_ADAPTER_NAME).collect(_context("household_ru.json")).records[0]

    assert record.value == 46.48
    assert record.item.value == "electricity"


def test_a_non_household_consumer_category_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("commercial.json"))

    assert caught.value.rule == "category"


def test_decreasing_tier_prices_are_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("decreasing_tiers.json"))

    assert caught.value.rule == "tiers"


def test_rising_tiers_are_copied_onto_the_record() -> None:
    record = get_adapter(LLM_ADAPTER_NAME).collect(_context("rising_tiers.json")).records[0]

    assert record.value == 6.267
    assert [(tier.up_to, tier.value) for tier in record.tiers] == [
        (101, 6.267),
        (301, 9.667),
        (None, 13.467),
    ]


def test_tier_bounds_that_do_not_increase_are_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("unordered_tiers.json"))

    assert caught.value.rule == "tiers"


def test_a_response_outside_the_schema_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as caught:
        get_adapter(LLM_ADAPTER_NAME).collect(_context("extra_field.json"))

    assert caught.value.rule == "schema"


def test_a_scan_uses_vision_and_does_not_need_the_quote_inside_the_bytes() -> None:
    record = (
        get_adapter(LLM_ADAPTER_NAME)
        .collect(
            _context(
                "accepted.json",
                mode="vision",
                url="https://regulator.example/scan.bin",
            )
        )
        .records[0]
    )

    assert record.method.value == "llm_vision"
    assert record.value == 46.48
    assert record.unit == "AMD/kWh"


def test_an_injected_client_supplies_the_model_response() -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.requests: list[LlmRequest] = []

        def complete(self, request: LlmRequest) -> str:
            self.requests.append(request)
            return (_FIXTURES / "accepted.json").read_text(encoding="utf-8")

    client = RecordingClient()
    output = LlmExtractAdapter(client=client).collect(_context(None))

    assert output.records[0].value == 46.48
    assert output.records[0].method.value == "llm_text"
    assert len(client.requests) == 1
    assert client.requests[0].mode == "text"
    assert b"46.48 AMD/kWh" in client.requests[0].document


def test_without_a_key_or_a_client_the_adapter_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(SourceUnavailable, match="LLM_API_KEY"):
        LlmExtractAdapter().collect(_context(None))


def test_a_key_without_a_client_still_does_not_call_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")

    with pytest.raises(SourceUnavailable, match="disabled stub") as caught:
        LlmExtractAdapter().collect(_context(None))

    assert "super-secret-key" not in str(caught.value)
