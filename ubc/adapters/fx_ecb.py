"""Monthly average PLN per 1 EUR from the ECB Data API.

The series ``EXR/M.PLN.EUR.SP00.A`` is already the monthly average. The month
comes from ``CollectContext.period``.
"""

from __future__ import annotations

import csv
from io import StringIO

from ubc.adapter import AdapterOutput, CollectContext, register_adapter
from ubc.adapters.fx_common import expect_currency, make_fx_record, setting_url
from ubc.schemas import ExtractionMethod


class EcbPlnAdapter:
    """ECB reference rate, Polish zloty per euro, monthly average."""

    name = "fx_ecb"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        expect_currency(ctx, "PLN")
        period = str(ctx.period)
        url = f"{setting_url(ctx)}?startPeriod={period}&endPeriod={period}&format=csvdata"
        text = ctx.fetcher.fetch_text(url)
        value = _observation(text, period)
        record = make_fx_record(
            ctx,
            value=value,
            note="ECB monthly average",
            method=ExtractionMethod.api,
            url=url,
            name="ECB",
        )
        return AdapterOutput(records=[record])


def _observation(text: str, period: str) -> float:
    rows = list(csv.DictReader(StringIO(text)))
    matched = [row for row in rows if row.get("TIME_PERIOD") == period and row.get("OBS_VALUE")]
    if len(matched) != 1:
        raise ValueError(f"ECB response has no single OBS_VALUE for {period}")
    return float(matched[0]["OBS_VALUE"])


fx_ecb = register_adapter(EcbPlnAdapter())
