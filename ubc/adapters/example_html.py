"""Template adapter. Not a real tariff source.

Copy this file when adding a source:

1. Read through ``ctx.fetcher`` (a saved fixture in tests, HTTP in the pipeline).
2. Return ``AdapterOutput`` with normalized records or building candidates.
3. Register the instance and add the module to ``ubc.adapter.ADAPTER_MODULES``.

The sample markup is one element with ``data-*`` attributes. Real adapters parse
the source's own HTML, PDF text, or JSON instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser

from ubc.adapter import AdapterOutput, CollectContext, register_adapter, require_source
from ubc.schemas import ITEM_CATEGORY, ExtractionMethod, Item, NormalizedRecord, SourceInfo

_REQUIRED = ("data-item", "data-value", "data-unit", "data-currency", "data-valid-from")


class _ValueParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value for key, value in attrs if value is not None}
        if "data-value" in data and self.found is None:
            self.found = data


class ExampleHtmlAdapter:
    """Build one normalized record from a single ``data-value`` element."""

    name = "example_html"

    def collect(self, ctx: CollectContext) -> AdapterOutput:
        # Real adapters use ctx.period to request that month (FX startPeriod,
        # Open-Meteo dates). This template only records that the month arrived.
        source = require_source(ctx)
        url = source.settings.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("example_html requires settings.url")
        parser = _ValueParser()
        parser.feed(ctx.fetcher.fetch_text(url))
        if parser.found is None:
            raise ValueError("example_html found no data-value element")
        missing = [key for key in _REQUIRED if key not in parser.found]
        if missing:
            raise ValueError(f"example_html element missing {', '.join(missing)}")
        attrs = parser.found
        item = Item(attrs["data-item"])
        record = NormalizedRecord(
            city=ctx.city.id,
            item=item,
            category=ITEM_CATEGORY[item],
            value=float(attrs["data-value"]),
            unit=attrs["data-unit"],
            currency=attrs["data-currency"],
            valid_from=attrs["data-valid-from"],
            source=SourceInfo(
                kind=source.kind,
                url=url,
                fetched_at=datetime.now(UTC),
                name=source.id,
            ),
            method=ExtractionMethod.regex,
            reliability=source.reliability,
            note=f"collected for {ctx.period}",
        )
        return AdapterOutput(records=[record])


example_html = register_adapter(ExampleHtmlAdapter())
