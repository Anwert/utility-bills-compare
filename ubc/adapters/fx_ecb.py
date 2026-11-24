"""Monthly average PLN per EUR from the ECB Data API.

The series ``EXR/M.PLN.EUR.SP00.A`` is already a monthly average, so this
adapter does not average days itself. ``settings.url`` is the series URL
without a query; the collection month is ``ctx.period``.
"""

from __future__ import annotations

import csv
import io

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.adapters.fx_common import fx_record, require_settings, with_query
from ubc.schemas import ExtractionMethod


class EcbFxAdapter:
    """One ECB monthly observation for the currency named in the series URL."""

    name = "fx_ecb"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        base, currency = require_settings(source)
        if f".{currency.value}." not in base:
            raise ValueError(f"fx_ecb series URL does not list {currency.value}")
        period = str(ctx.period)
        url = with_query(
            base,
            [
                ("startPeriod", period),
                ("endPeriod", period),
                ("format", "csvdata"),
            ],
        )
        value = _observation(ctx.fetcher.fetch_text(url), period, currency.value)
        record = fx_record(
            currency=currency,
            value=value,
            valid_from=ctx.period.first_day,
            valid_to=ctx.period.last_day,
            source=source,
            url=url,
            method=ExtractionMethod.api,
            note="ECB monthly average",
        )
        return AdapterOutput(records=[record])


def _observation(text: str, period: str, currency: str) -> float:
    rows = list(csv.DictReader(io.StringIO(text)))
    matched = [row for row in rows if (row.get("TIME_PERIOD") or "").strip() == period]
    if len(matched) != 1:
        raise ValueError(f"fx_ecb expected one {period} observation, found {len(matched)}")
    row = matched[0]
    if (row.get("CURRENCY") or "").strip() != currency:
        raise ValueError(f"fx_ecb observation is not {currency}")
    if (row.get("CURRENCY_DENOM") or "").strip() != "EUR":
        raise ValueError("fx_ecb observation is not quoted per EUR")
    raw = (row.get("OBS_VALUE") or "").strip()
    if not raw:
        raise ValueError("fx_ecb observation has no OBS_VALUE")
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError("fx_ecb OBS_VALUE is not a number") from exc


fx_ecb = register_adapter(EcbFxAdapter())
