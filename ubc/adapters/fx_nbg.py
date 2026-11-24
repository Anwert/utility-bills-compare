"""Monthly average GEL per EUR from the National Bank of Georgia JSON API.

The API returns one day per request. Days without a new fixing repeat the
previous published rate and keep that earlier date on the payload. The month's
figure is the mean of every calendar day in ``ctx.period``, repeats included.
"""

from __future__ import annotations

import json
import statistics
from datetime import timedelta

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.adapters.fx_common import fx_record, require_domestic, require_settings, with_query
from ubc.schemas import Currency, ExtractionMethod


class NbgFxAdapter:
    """Arithmetic mean of the NBG's daily GEL-per-EUR rates in ``ctx.period``."""

    name = "fx_nbg"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        base, currency = require_settings(source)
        require_domestic(self.name, currency, Currency.GEL)
        rates: list[float] = []
        day = ctx.period.first_day
        while day <= ctx.period.last_day:
            url = with_query(base, [("currencies", "EUR"), ("date", day.isoformat())])
            try:
                payload = json.loads(ctx.fetcher.fetch_text(url))
            except json.JSONDecodeError as exc:
                raise ValueError(f"fx_nbg response for {day.isoformat()} is not JSON") from exc
            rates.append(_day_rate(payload))
            day += timedelta(days=1)
        record = fx_record(
            currency=currency,
            value=statistics.mean(rates),
            valid_from=ctx.period.first_day,
            valid_to=ctx.period.last_day,
            source=source,
            url=base,
            method=ExtractionMethod.api,
            note=f"NBG mean of {len(rates)} days",
        )
        return AdapterOutput(records=[record])


def _day_rate(payload: object) -> float:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ValueError("fx_nbg response is not one day's rates")
    currencies = payload[0].get("currencies")
    if not isinstance(currencies, list):
        raise ValueError("fx_nbg response has no currencies")
    eur = [item for item in currencies if isinstance(item, dict) and item.get("code") == "EUR"]
    if len(eur) != 1:
        raise ValueError("fx_nbg response has no single EUR rate")
    try:
        rate = float(eur[0]["rate"])
        quantity = float(eur[0]["quantity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("fx_nbg EUR rate is missing") from exc
    if rate <= 0 or quantity <= 0:
        raise ValueError("fx_nbg EUR rate must be positive")
    return rate / quantity


fx_nbg = register_adapter(NbgFxAdapter())
