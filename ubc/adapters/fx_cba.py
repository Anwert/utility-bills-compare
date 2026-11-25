"""Monthly average AMD per 1 EUR from the Central Bank of Armenia.

``ExchangeRatesByDateRangeByISO`` is a SOAP call and returns one rate per
banking day. The adapter averages those days for ``CollectContext.period``.
The month is also on the request URL so a saved fixture can be selected
without opening a socket; the bank reads the dates from the SOAP body.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from statistics import mean

from ubc.adapter import AdapterOutput, CollectContext, register_adapter
from ubc.adapters.fx_common import expect_currency, make_fx_record, setting_url
from ubc.fetch import ContentFetcher
from ubc.schemas import ExtractionMethod, YearMonth

_SOAP_ACTION = "http://www.cba.am/ExchangeRatesByDateRangeByISO"


class CbaAmdAdapter:
    """Central Bank of Armenia, dram per euro, mean of the month's daily rates."""

    name = "fx_cba"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        expect_currency(ctx, "AMD")
        endpoint = setting_url(ctx)
        if "?" in endpoint:
            raise ValueError("fx_cba settings.url is the SOAP endpoint without a query")
        rates = list(daily_rates(ctx.fetcher, endpoint, ctx.period, ("EUR",))["EUR"].values())
        record = make_fx_record(
            ctx,
            value=round(mean(rates), 4),
            note=f"CBA mean of {len(rates)} daily rates",
            method=ExtractionMethod.api,
            url=endpoint,
            name="Central Bank of Armenia",
        )
        return AdapterOutput(records=[record])


def daily_rates(
    fetcher: ContentFetcher, endpoint: str, period: YearMonth, codes: Sequence[str]
) -> dict[str, dict[str, float]]:
    """Drams for one unit of each currency, by banking day of the month."""

    start = period.first_day.isoformat()
    end = period.last_day.isoformat()
    asked = "" if tuple(codes) == ("EUR",) else f"ISOCodes={','.join(codes)}&"
    text = fetcher.post_text(
        f"{endpoint}?{asked}DateFrom={start}&DateTo={end}",
        _soap_body(",".join(codes), start, end),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": _SOAP_ACTION,
        },
    )
    return _rates_by_day(text, codes)


def _soap_body(codes: str, start: str, end: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        '<ExchangeRatesByDateRangeByISO xmlns="http://www.cba.am/">'
        f"<ISOCodes>{codes}</ISOCodes>"
        f"<DateFrom>{start}</DateFrom>"
        f"<DateTo>{end}</DateTo>"
        "</ExchangeRatesByDateRangeByISO>"
        "</soap:Body>"
        "</soap:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _rates_by_day(text: str, codes: Sequence[str]) -> dict[str, dict[str, float]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("CBA response is not XML") from exc
    rates: dict[str, dict[str, float]] = {code: {} for code in codes}
    for row in root.iter():
        if _local(row.tag) != "ExchangeRatesByRange":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in row}
        code = fields.get("ISO", "")
        if code not in rates:
            continue
        amount = float(fields.get("Amount") or "1")
        rate = float(fields["Rate"])
        if amount <= 0 or rate <= 0:
            raise ValueError("CBA rate or its amount is not positive")
        rates[code][fields.get("RateDate") or str(len(rates[code]))] = rate / amount
    for code, days in rates.items():
        if not days:
            raise ValueError(f"CBA response has no {code} rates")
    return rates


fx_cba = register_adapter(CbaAmdAdapter())
