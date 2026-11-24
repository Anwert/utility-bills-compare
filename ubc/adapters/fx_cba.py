"""Monthly average AMD per EUR from the Central Bank of Armenia SOAP API.

``ExchangeRatesByDateRangeByISO`` returns one AMD rate per published day.
October 2026 has 22 of them (the bank does not publish a rate every calendar
day). The mean of those daily rates, each divided by its ``Amount``, is the
month's figure. The period is both the SOAP range and the query on the URL,
so a saved fixture can tell months apart.
"""

from __future__ import annotations

import statistics
import xml.etree.ElementTree as ET
from datetime import date

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.adapters.fx_common import fx_record, require_domestic, require_settings, with_query
from ubc.schemas import Currency, ExtractionMethod

_SOAP_ACTION = "http://www.cba.am/ExchangeRatesByDateRangeByISO"


class CbaFxAdapter:
    """Arithmetic mean of the CBA's daily AMD-per-EUR rates in ``ctx.period``."""

    name = "fx_cba"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        base, currency = require_settings(source)
        require_domestic(self.name, currency, Currency.AMD)
        start = ctx.period.first_day
        end = ctx.period.last_day
        url = with_query(
            base,
            [
                ("DateFrom", start.isoformat()),
                ("DateTo", end.isoformat()),
                ("ISOCodes", "EUR"),
            ],
        )
        payload = ctx.fetcher.post_bytes(
            url,
            _envelope(start, end),
            {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": _SOAP_ACTION,
            },
        )
        rates = _daily_rates(payload, start, end)
        record = fx_record(
            currency=currency,
            value=statistics.mean(rates),
            valid_from=start,
            valid_to=end,
            source=source,
            url=url,
            method=ExtractionMethod.api,
            note=f"CBA mean of {len(rates)} daily rates",
        )
        return AdapterOutput(records=[record])


def _envelope(start: date, end: date) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        '<ExchangeRatesByDateRangeByISO xmlns="http://www.cba.am/">'
        "<ISOCodes>EUR</ISOCodes>"
        f"<DateFrom>{start.isoformat()}</DateFrom>"
        f"<DateTo>{end.isoformat()}</DateTo>"
        "</ExchangeRatesByDateRangeByISO>"
        "</soap:Body></soap:Envelope>"
    )
    return body.encode("utf-8")


def _daily_rates(payload: bytes, start: date, end: date) -> list[float]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("fx_cba response is not XML") from exc
    start_text = start.isoformat()
    end_text = end.isoformat()
    rates: list[float] = []
    for element in root.iter():
        if _local(element.tag) != "ExchangeRatesByRange":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in element}
        iso = fields.get("ISO", "")
        if iso and iso != "EUR":
            continue
        published = fields.get("RateDate", "")[:10]
        if published < start_text or published > end_text:
            continue
        rates.append(_per_euro(fields))
    if not rates:
        raise ValueError("fx_cba returned no EUR rates for the month")
    return rates


def _per_euro(fields: dict[str, str]) -> float:
    raw_rate = fields.get("Rate", "")
    raw_amount = fields.get("Amount", "")
    try:
        rate = float(raw_rate)
        amount = float(raw_amount) if raw_amount else 1.0
    except ValueError as exc:
        raise ValueError("fx_cba rate is not a number") from exc
    if rate <= 0 or amount <= 0:
        raise ValueError("fx_cba rate and amount must be positive")
    return rate / amount


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


fx_cba = register_adapter(CbaFxAdapter())
