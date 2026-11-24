"""Current middle rate of the Serbian dinar from the National Bank page.

This is the fallback for the Eurostat monthly average, not a monthly average
itself. The page publishes one rate list for one day (RSD per unit of foreign
currency; EUR's unit is 1). ``valid_from`` is that day, taken from the page,
not the collection month.
"""

from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.adapters.fx_common import fx_record, require_domestic, require_settings
from ubc.schemas import Currency, ExtractionMethod

_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


class NbsFxAdapter:
    """The EUR row of the NBS current middle-rate table."""

    name = "fx_nbs"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        source = require_source(ctx)
        url, currency = require_settings(source)
        require_domestic(self.name, currency, Currency.RSD)
        parsed = _Page()
        parsed.feed(ctx.fetcher.fetch_text(url))
        rate_date, per_eur = _eur_rate(parsed)
        record = fx_record(
            currency=currency,
            value=per_eur,
            valid_from=rate_date,
            valid_to=rate_date,
            source=source,
            url=url,
            method=ExtractionMethod.regex,
            note="NBS current middle rate (cross-check)",
        )
        return AdapterOutput(records=[record])


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_cell = False
        self._cell: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td":
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_cell:
            self._row.append("".join(self._cell).strip())
            self._in_cell = False
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._in_cell:
            self._cell.append(data)


def _eur_rate(page: _Page) -> tuple[date, float]:
    text = "".join(page.text)
    dates = {date(int(year), int(month), int(day)) for day, month, year in _DATE.findall(text)}
    if len(dates) != 1:
        raise ValueError("fx_nbs page does not name one rate date")
    eur_rows = [row for row in page.rows if row and row[0] == "EUR"]
    if len(eur_rows) != 1:
        raise ValueError("fx_nbs page does not have one EUR row")
    row = eur_rows[0]
    if len(row) < 5:
        raise ValueError("fx_nbs EUR row is missing the middle rate")
    quantity = _number(row[3])
    rate = _number(row[4])
    if quantity <= 0 or rate <= 0:
        raise ValueError("fx_nbs EUR rate must be positive")
    return dates.pop(), rate / quantity


def _number(text: str) -> float:
    cleaned = text.strip().replace("\xa0", "").replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(f"fx_nbs could not read a number from {text!r}") from exc


fx_nbs = register_adapter(NbsFxAdapter())
