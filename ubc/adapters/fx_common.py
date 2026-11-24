"""Fields shared by the exchange-rate adapters.

Each adapter parses its own page. This module only reads the two settings every
FX source has (``url`` and ``currency``) and fills one normalized record.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import urlencode

from ubc.schemas import (
    ITEM_CATEGORY,
    Currency,
    ExtractionMethod,
    Item,
    NormalizedRecord,
    SourceConfig,
    SourceInfo,
)


def require_settings(source: SourceConfig) -> tuple[str, Currency]:
    """``settings.url`` (https, no query) and ``settings.currency``."""

    url = source.settings.get("url")
    raw_currency = source.settings.get("currency")
    if not isinstance(url, str) or not url.startswith("https://") or "?" in url:
        raise ValueError(f"{source.adapter} requires an https settings.url without a query")
    if not isinstance(raw_currency, str):
        raise ValueError(f"{source.adapter} requires settings.currency")
    try:
        currency = Currency(raw_currency)
    except ValueError as exc:
        raise ValueError(f"{source.adapter} currency {raw_currency!r} is not supported") from exc
    return url, currency


def require_domestic(adapter: str, currency: Currency, expected: Currency) -> None:
    """The configured currency is the domestic one this source quotes per 1 euro."""

    if currency is not expected:
        raise ValueError(f"{adapter} quotes {expected.value} per EUR, not {currency.value}")


def with_query(base: str, query: list[tuple[str, str]]) -> str:
    return f"{base}?{urlencode(query)}"


def fx_record(
    *,
    currency: Currency,
    value: float,
    valid_from: date,
    valid_to: date,
    source: SourceConfig,
    url: str,
    method: ExtractionMethod,
    note: str,
) -> NormalizedRecord:
    """One ``fx`` record for every city. ``value`` is domestic currency per 1 euro."""

    if value <= 0:
        raise ValueError(f"{source.adapter} rate must be positive")
    return NormalizedRecord(
        city="all",
        item=Item.fx,
        category=ITEM_CATEGORY[Item.fx],
        value=value,
        unit=f"{currency.value}/EUR",
        currency=currency,
        valid_from=valid_from,
        valid_to=valid_to,
        source=SourceInfo(
            kind=source.kind,
            url=url,
            fetched_at=datetime.now(UTC),
            name=source.id,
        ),
        method=method,
        reliability=source.reliability,
        note=note,
    )
