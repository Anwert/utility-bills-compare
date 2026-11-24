"""LLM extraction wrapper. Disabled unless a saved response or a client is supplied.

The model itself is not called from this module. Tests pass a saved JSON
response through ``settings.model_response_url`` (read by the fixture fetcher)
or inject an ``LlmClient``. Without ``LLM_API_KEY`` and without either of
those, ``collect`` refuses and the selection policy moves on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from pydantic import Field, ValidationError

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.schemas import (
    CHARGE_ITEMS,
    ITEM_CATEGORY,
    CityId,
    ContractModel,
    ExtractionMethod,
    Finite,
    FlexDate,
    Item,
    NormalizedRecord,
    SourceInfo,
    Tier,
)
from ubc.secrets import LLM_ADAPTER_NAME, SecretConfig, SourceUnavailable

_CURRENCIES = ("AMD", "GEL", "RSD", "PLN", "EUR", "local", "tetri")
_MEASURES = (
    "kWh",
    "m3",
    "m2",
    "m2/month",
    "m2/year",
    "person/month",
    "month",
    "GJ",
    "kW",
    "MW/month",
    "day",
)
ALLOWED_UNITS = frozenset(
    f"{currency}/{measure}" for currency in _CURRENCIES for measure in _MEASURES
)

# Sanity bounds in local currency, wide enough for a per-unit price and a monthly
# bill, tight enough to drop a hallucinated magnitude. Not a published tariff.
_SANITY_RANGE: dict[CityId, tuple[float, float]] = {
    CityId.yerevan: (0.01, 50_000),
    CityId.tbilisi: (0.001, 5_000),
    CityId.belgrade: (0.01, 100_000),
    CityId.warsaw: (0.001, 20_000),
    CityId.lisbon: (0.001, 2_000),
    CityId.berlin: (0.001, 2_000),
}


class ExtractionRejected(Exception):
    """The model answer failed a check. ``rule`` names which one."""

    def __init__(self, rule: str) -> None:
        self.rule = rule
        super().__init__(f"LLM answer rejected: {rule}")


class LlmRequest:
    """What a model client receives. ``document`` is the page text or the image bytes."""

    def __init__(self, prompt: str, document: bytes, mode: str) -> None:
        self.prompt = prompt
        self.document = document
        self.mode = mode


class LlmClient(Protocol):
    def complete(self, request: LlmRequest) -> str: ...


class LlmAnswer(ContractModel):
    """Strict shape the model must return. Extra keys are rejected."""

    value: Finite
    unit: str
    valid_from: FlexDate
    valid_to: FlexDate | None = None
    consumer_category: str
    quote: str
    tiers: list[Tier] = Field(default_factory=list)


class LlmExtractAdapter:
    """Turn one model answer plus the source page into a normalized record."""

    name = LLM_ADAPTER_NAME

    def __init__(self, client: LlmClient | None = None) -> None:
        self.client = client

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        url = _required_text(source.settings, "url")
        item = Item(_required_text(source.settings, "item"))
        mode = str(source.settings.get("mode") or "text")
        document = _load_document(ctx, url, mode)
        try:
            answer = LlmAnswer.model_validate_json(_model_response(self, ctx, document, mode))
        except ValidationError as exc:
            raise ExtractionRejected("schema") from exc
        if answer.unit not in ALLOWED_UNITS:
            raise ExtractionRejected("unit")
        if not _in_range(ctx.city.id, item, answer.value):
            raise ExtractionRejected("range")
        if not answer.quote.strip() or (
            mode == "text" and not _quote_in_text(answer.quote, document)
        ):
            raise ExtractionRejected("quote")
        if not _date_ok(answer.valid_from, answer.valid_to, ctx.period.last_day):
            raise ExtractionRejected("date")
        if answer.consumer_category.strip().casefold() not in {"household", "бытовые"}:
            raise ExtractionRejected("category")
        tier_rule = _tier_rule(answer.tiers, ctx.city.id, item)
        if tier_rule is not None:
            raise ExtractionRejected(tier_rule)
        record = NormalizedRecord(
            city=ctx.city.id,
            item=item,
            category=ITEM_CATEGORY[item],
            value=answer.value,
            unit=answer.unit,
            currency=ctx.city.currency,
            tiers=answer.tiers,
            valid_from=answer.valid_from,
            valid_to=answer.valid_to,
            source=SourceInfo(
                kind=source.kind,
                url=url,
                fetched_at=datetime.now(UTC),
                name=source.id,
            ),
            method=ExtractionMethod.llm_vision if mode == "vision" else ExtractionMethod.llm_text,
            reliability=source.reliability,
            note=answer.quote,
        )
        return AdapterOutput(records=[record])


def _date_ok(valid_from: date, valid_to: date | None, as_of: date) -> bool:
    """Start date may be at most three calendar months after the collected month."""

    if valid_from > _add_months(as_of, 3):
        return False
    return valid_to is None or valid_to >= valid_from


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def _tier_rule(tiers: list[Tier], city: CityId, item: Item) -> str | None:
    """``tiers`` when steps do not rise, ``range`` when a step price is implausible."""

    previous_price: float | None = None
    previous_bound: float | None = None
    for index, tier in enumerate(tiers):
        if not _in_range(city, item, tier.value):
            return "range"
        if previous_price is not None and tier.value < previous_price:
            return "tiers"
        previous_price = tier.value
        last = index == len(tiers) - 1
        if tier.up_to is None:
            if not last:
                return "tiers"
            continue
        if previous_bound is not None and tier.up_to <= previous_bound:
            return "tiers"
        previous_bound = tier.up_to
    return None


def _quote_in_text(quote: str, document: bytes) -> bool:
    folded = " ".join(quote.split())
    if not folded:
        return False
    source = " ".join(document.decode("utf-8", "replace").split())
    return folded in source


def _in_range(city: CityId, item: Item, value: float) -> bool:
    if item not in CHARGE_ITEMS:
        return False
    low, high = _SANITY_RANGE[city]
    return low <= value <= high


def _required_text(settings: dict[str, object], key: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"llm_extract requires settings.{key}")
    return value


def _load_document(ctx: CollectContext, url: str, mode: str) -> bytes:
    if mode == "vision":
        body = ctx.fetcher.fetch_bytes(url)
    elif mode == "text":
        body = ctx.fetcher.fetch_text(url).encode("utf-8")
    else:
        raise ValueError("llm_extract settings.mode must be text or vision")
    if not body.strip():
        raise ValueError("llm_extract source document is empty")
    return body


def _model_response(
    adapter: LlmExtractAdapter,
    ctx: CollectContext,
    document: bytes,
    mode: str,
) -> str:
    source = require_source(ctx)
    saved = source.settings.get("model_response_url")
    if isinstance(saved, str) and saved.strip():
        return ctx.fetcher.fetch_text(saved)
    if adapter.client is not None:
        prompt = (
            "Extract the household tariff. Reply with JSON keys "
            "value, unit, valid_from, valid_to, consumer_category, quote, tiers."
        )
        return adapter.client.complete(LlmRequest(prompt, document, mode))
    if not SecretConfig.from_env().llm_ready:
        raise SourceUnavailable("LLM source is disabled: LLM_API_KEY is not set")
    raise SourceUnavailable("LLM client is a disabled stub and does not call a model")


llm_extract = register_adapter(LlmExtractAdapter())
