"""Monthly average RSD per 1 EUR from Eurostat ``ert_bil_eur_m``.

The series is the National Bank of Serbia average, published without a
registration key. ``statinfo=AVG`` and ``time`` is ``CollectContext.period``.
"""

from __future__ import annotations

import json

from ubc.adapter import AdapterOutput, CollectContext, register_adapter
from ubc.adapters.fx_common import expect_currency, make_fx_record, setting_url
from ubc.schemas import ExtractionMethod


class EurostatRsdAdapter:
    """Eurostat bilateral euro rate, dinar per euro, one monthly average."""

    name = "fx_eurostat"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        currency = expect_currency(ctx, "RSD")
        base = setting_url(ctx)
        if "?" in base:
            raise ValueError("fx_eurostat settings.url has no query of its own")
        period = str(ctx.period)
        url = f"{base}?currency={currency.value}&statinfo=AVG&time={period}"
        value = _monthly_average(ctx.fetcher.fetch_text(url), period, currency.value)
        record = make_fx_record(
            ctx,
            value=value,
            note="Eurostat monthly average (source NBS)",
            method=ExtractionMethod.api,
            url=url,
            name="Eurostat",
        )
        return AdapterOutput(records=[record])


def _monthly_average(text: str, period: str, currency: str) -> float:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Eurostat response is not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Eurostat response is not a dataset")
    _require_category(payload, "time", period)
    _require_category(payload, "currency", currency)
    value = payload.get("value")
    if isinstance(value, dict) and len(value) == 1:
        return float(next(iter(value.values())))
    raise ValueError(f"Eurostat response is not a single monthly rate for {period}")


def _require_category(payload: dict, dimension: str, code: str) -> None:
    index = payload.get("dimension", {}).get(dimension, {}).get("category", {}).get("index", {})
    if not isinstance(index, dict) or code not in index:
        raise ValueError(f"Eurostat response has no {dimension} {code}")


fx_eurostat = register_adapter(EurostatRsdAdapter())
