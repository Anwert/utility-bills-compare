"""Current middle rate, RSD per 1 EUR, from the National Bank of Serbia.

This is the fallback for the Eurostat monthly average. The page is the list
in force today, not an average of the collected month, so ``valid_from`` is
the list date printed on the page.
"""

from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser

from ubc.adapter import AdapterOutput, CollectContext, register_adapter
from ubc.adapters.fx_common import expect_currency, make_fx_record, setting_url
from ubc.schemas import ExtractionMethod

_LIST_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


class NbsRsdAdapter:
    """National Bank of Serbia current middle rate, dinar per euro."""

    name = "fx_nbs"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        expect_currency(ctx, "RSD")
        url = setting_url(ctx)
        page = _Page()
        page.feed(ctx.fetcher.fetch_text(url))
        value, valid_from = _eur_rate(page)
        record = make_fx_record(
            ctx,
            value=value,
            note="NBS current middle rate (cross-check)",
            method=ExtractionMethod.regex,
            url=url,
            valid_from=valid_from,
            name="National Bank of Serbia",
        )
        return AdapterOutput(records=[record])


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._cell is not None:
            self._cell.append(data)


def _eur_rate(page: _Page) -> tuple[float, date]:
    row = next((cells for cells in page.rows if cells and cells[0] == "EUR"), None)
    if row is None or len(row) < 2:
        raise ValueError("NBS page has no EUR middle rate")
    per = _number(row[-2])
    if per == 0:
        raise ValueError("NBS EUR unit is zero")
    listed = _LIST_DATE.search("".join(page.text))
    if listed is None:
        raise ValueError("NBS page has no exchange-list date")
    day, month, year = (int(part) for part in listed.groups())
    return _number(row[-1]) / per, date(year, month, day)


def _number(text: str) -> float:
    return float(text.replace("\xa0", "").replace(" ", "").replace(",", "."))


fx_nbs = register_adapter(NbsRsdAdapter())
