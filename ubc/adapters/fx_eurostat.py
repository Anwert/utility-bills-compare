"""Monthly average of a national currency per EUR from Eurostat.

Dataset ``ert_bil_eur_m`` with ``statinfo=AVG`` is the monthly average already.
For the dinar the series is the National Bank of Serbia's average, republished
without a Eurostat account. ``settings.currency`` selects the series.
"""

from __future__ import annotations

import json

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.adapters.fx_common import fx_record, require_settings, with_query
from ubc.schemas import ExtractionMethod


class EurostatFxAdapter:
    """The Eurostat monthly average for ``settings.currency`` in ``ctx.period``."""

    name = "fx_eurostat"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        base, currency = require_settings(source)
        period = str(ctx.period)
        url = with_query(
            base,
            [
                ("currency", currency.value),
                ("statinfo", "AVG"),
                ("time", period),
            ],
        )
        try:
            payload = json.loads(ctx.fetcher.fetch_text(url))
        except json.JSONDecodeError as exc:
            raise ValueError("fx_eurostat response is not JSON") from exc
        value = _average(payload, currency.value, period)
        record = fx_record(
            currency=currency,
            value=value,
            valid_from=ctx.period.first_day,
            valid_to=ctx.period.last_day,
            source=source,
            url=url,
            method=ExtractionMethod.api,
            note="Eurostat monthly average (source NBS)",
        )
        return AdapterOutput(records=[record])


def _average(payload: object, currency: str, period: str) -> float:
    if not isinstance(payload, dict):
        raise ValueError("fx_eurostat response is not an object")
    _require_code(payload, "currency", currency)
    _require_code(payload, "statinfo", "AVG")
    _require_code(payload, "time", period)
    values = payload.get("value")
    if not isinstance(values, dict) or len(values) != 1:
        raise ValueError("fx_eurostat expected one monthly average")
    raw = next(iter(values.values()))
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("fx_eurostat average is not a number") from exc


def _require_code(payload: dict[str, object], name: str, expected: str) -> None:
    dimension = payload.get("dimension")
    if not isinstance(dimension, dict):
        raise ValueError("fx_eurostat response has no dimensions")
    entry = dimension.get(name)
    if not isinstance(entry, dict):
        raise ValueError(f"fx_eurostat response has no {name} dimension")
    category = entry.get("category")
    if not isinstance(category, dict):
        raise ValueError(f"fx_eurostat response has no {name} category")
    index = category.get("index")
    if not isinstance(index, dict) or expected not in index:
        raise ValueError(f"fx_eurostat response is not {name}={expected}")


fx_eurostat = register_adapter(EurostatFxAdapter())
