"""Monthly average GEL per 1 EUR from the National Bank of Georgia.

The public JSON endpoint returns one day. The adapter requests every calendar
day of ``CollectContext.period`` and averages ``rate / quantity`` for EUR.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import mean

from ubc.adapter import AdapterOutput, CollectContext, register_adapter
from ubc.adapters.fx_common import expect_currency, make_fx_record, setting_url
from ubc.schemas import ExtractionMethod


class NbgGelAdapter:
    """National Bank of Georgia, lari per euro, mean of every day in the month."""

    name = "fx_nbg"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        expect_currency(ctx, "GEL")
        base = setting_url(ctx)
        rates = [_eur_per_unit(ctx.fetcher.fetch_text(_day_url(base, day))) for day in _days(ctx)]
        record = make_fx_record(
            ctx,
            value=round(mean(rates), 4),
            note=f"NBG mean of {len(rates)} days",
            method=ExtractionMethod.api,
            url=_source_url(base),
            name="National Bank of Georgia",
        )
        return AdapterOutput(records=[record])


def _days(ctx: CollectContext) -> list[date]:
    day = ctx.period.first_day
    last = ctx.period.last_day
    found: list[date] = []
    while day <= last:
        found.append(day)
        day += timedelta(days=1)
    return found


def _day_url(base: str, day: date) -> str:
    root = base if base.endswith("/") else f"{base}/"
    return f"{root}?currencies=EUR&date={day.isoformat()}"


def _source_url(base: str) -> str:
    root = base if base.endswith("/") else f"{base}/"
    return f"{root}?currencies=EUR&date="


def _eur_per_unit(text: str) -> float:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("NBG response is not JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("NBG response has no rate for this day")
    currencies = payload[0].get("currencies") if isinstance(payload[0], dict) else None
    if not isinstance(currencies, list):
        raise ValueError("NBG response has no currencies")
    eur = next((item for item in currencies if item.get("code") == "EUR"), None)
    if not isinstance(eur, dict):
        raise ValueError("NBG response has no EUR rate")
    quantity = float(eur["quantity"])
    if quantity == 0:
        raise ValueError("NBG quantity is zero")
    return float(eur["rate"]) / quantity


fx_nbg = register_adapter(NbgGelAdapter())
