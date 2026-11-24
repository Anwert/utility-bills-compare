"""Monthly average AMD per 1 EUR from the Central Bank of Armenia.

``ExchangeRatesByDateRangeByISO`` is a SOAP call and returns one rate per
banking day. The adapter averages those days for ``CollectContext.period``.
The month is also on the request URL so a saved fixture can be selected
without opening a socket; the bank reads the dates from the SOAP body.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from statistics import mean

from ubc.adapter import AdapterOutput, CollectContext, register_adapter
from ubc.adapters.fx_common import expect_currency, make_fx_record, setting_url
from ubc.schemas import ExtractionMethod

_SOAP_ACTION = "http://www.cba.am/ExchangeRatesByDateRangeByISO"


class CbaAmdAdapter:
    """Central Bank of Armenia, dram per euro, mean of the month's daily rates."""

    name = "fx_cba"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        expect_currency(ctx, "AMD")
        endpoint = setting_url(ctx)
        if "?" in endpoint:
            raise ValueError("fx_cba settings.url is the SOAP endpoint without a query")
        start = ctx.period.first_day.isoformat()
        end = ctx.period.last_day.isoformat()
        request_url = f"{endpoint}?DateFrom={start}&DateTo={end}"
        text = ctx.fetcher.post_text(
            request_url,
            _soap_body(start, end),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": _SOAP_ACTION,
            },
        )
        rates = _eur_rates(text)
        record = make_fx_record(
            ctx,
            value=round(mean(rates), 4),
            note=f"CBA mean of {len(rates)} daily rates",
            method=ExtractionMethod.api,
            url=endpoint,
            name="Central Bank of Armenia",
        )
        return AdapterOutput(records=[record])


def _soap_body(start: str, end: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        '<ExchangeRatesByDateRangeByISO xmlns="http://www.cba.am/">'
        "<ISOCodes>EUR</ISOCodes>"
        f"<DateFrom>{start}</DateFrom>"
        f"<DateTo>{end}</DateTo>"
        "</ExchangeRatesByDateRangeByISO>"
        "</soap:Body>"
        "</soap:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _eur_rates(text: str) -> list[float]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("CBA response is not XML") from exc
    rates: list[float] = []
    for row in root.iter():
        if _local(row.tag) != "ExchangeRatesByRange":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in row}
        if fields.get("ISO") != "EUR":
            continue
        amount = float(fields.get("Amount") or "1")
        if amount == 0:
            raise ValueError("CBA rate amount is zero")
        rates.append(float(fields["Rate"]) / amount)
    if not rates:
        raise ValueError("CBA response has no EUR rates")
    return rates


fx_cba = register_adapter(CbaAmdAdapter())
