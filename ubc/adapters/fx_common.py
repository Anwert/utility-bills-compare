"""Shared pieces for official monthly EUR exchange-rate adapters.

Each adapter still owns its source. This only builds the normalized record
and reads the URL the city file configures.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from ubc.adapter import CollectContext, require_source
from ubc.schemas import (
    ITEM_CATEGORY,
    Currency,
    ExtractionMethod,
    Item,
    NormalizedRecord,
    SourceInfo,
)


def setting_url(ctx: CollectContext) -> str:
    """The http(s) endpoint from ``settings.url``. Currency must match the city."""

    source = require_source(ctx)
    url = source.settings.get("url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise ValueError(f"{source.adapter} requires an http(s) settings.url")
    configured = source.settings.get("currency")
    if configured is not None and configured != ctx.city.currency.value:
        raise ValueError(
            f"{source.adapter} settings.currency is {configured}, "
            f"city {ctx.city.id.value} uses {ctx.city.currency.value}"
        )
    return url


def expect_currency(ctx: CollectContext, code: str) -> Currency:
    """The adapter's currency. A city with another currency is a miswired source."""

    if ctx.city.currency.value != code:
        raise ValueError(f"{code} exchange-rate adapter cannot collect {ctx.city.currency.value}")
    return Currency(code)


def make_fx_record(
    ctx: CollectContext,
    *,
    value: float,
    note: str,
    method: ExtractionMethod,
    url: str,
    valid_from: date | None = None,
    name: str | None = None,
) -> NormalizedRecord:
    """One ``fx`` record shared by every city (``city='all'``)."""

    source = require_source(ctx)
    currency = ctx.city.currency
    return NormalizedRecord(
        city="all",
        item=Item.fx,
        category=ITEM_CATEGORY[Item.fx],
        value=value,
        unit=f"{currency.value}/EUR",
        currency=currency,
        valid_from=valid_from if valid_from is not None else ctx.period.first_day,
        source=SourceInfo(
            kind=source.kind,
            url=url,
            fetched_at=datetime.now(UTC),
            name=name if name is not None else source.id,
        ),
        method=method,
        reliability=source.reliability,
        note=note,
    )
