"""Registry and the example adapter, exercised on a saved HTML fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from ubc.adapter import AdapterOutput, CollectContext, Registry, get_adapter
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import Reliability, SourceConfig, SourceKind


def _context(url: str, fetcher: FixtureFetcher) -> CollectContext:
    return CollectContext(
        city=load_city("yerevan"),
        source=SourceConfig(
            id="example_waste",
            adapter="example_html",
            kind=SourceKind.official,
            enabled=True,
            reliability=Reliability.official,
            settings={"url": url},
        ),
        fetcher=fetcher,
    )


def test_example_adapter_returns_the_fixture_record() -> None:
    root = REPO_ROOT / "tests" / "fixtures" / "example"
    output = get_adapter("example_html").collect(
        _context("https://example.test/tariff.html", FixtureFetcher(root))
    )

    assert isinstance(output, AdapterOutput)
    assert output.candidates == []
    assert len(output.records) == 1
    record = output.records[0]
    assert record.city.value == "yerevan"
    assert record.item.value == "waste"
    assert record.category.value == "utility"
    assert record.value == 13
    assert record.unit == "AMD/m2/month"
    assert record.currency is not None and record.currency.value == "AMD"
    assert record.valid_from.isoformat() == "2026-01-01"
    assert record.method.value == "regex"
    assert record.reliability is Reliability.official
    assert record.source.url == "https://example.test/tariff.html"
    assert record.source.fetched_at.tzinfo is not None


def test_example_adapter_rejects_a_page_without_a_value(tmp_path: Path) -> None:
    (tmp_path / "empty.html").write_text("<p>no tariff</p>", encoding="utf-8")
    fetcher = FixtureFetcher(tmp_path, {"https://example.test/empty": "empty.html"})

    with pytest.raises(ValueError, match="data-value"):
        get_adapter("example_html").collect(_context("https://example.test/empty", fetcher))


def test_unknown_adapter_names_the_ones_that_exist() -> None:
    with pytest.raises(KeyError, match="example_html"):
        get_adapter("not_an_adapter")


def test_registry_rejects_a_second_adapter_with_the_same_name() -> None:
    class Dummy:
        name = "dummy"

        def collect(self, ctx: CollectContext) -> AdapterOutput:
            return AdapterOutput()

    registry = Registry()
    first = Dummy()
    registry.register(first)
    registry.register(first)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Dummy())
