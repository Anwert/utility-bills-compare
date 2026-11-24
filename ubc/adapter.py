"""Adapter interface and registry.

An adapter turns one configured source into normalized records or building
candidates. It does not choose among sources, calculate a cell, or publish.
The registry name is ``SourceConfig.adapter`` in the city file.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Protocol

from ubc.fetch import ContentFetcher
from ubc.schemas import (
    BuildingCandidate,
    CityConfig,
    ListingSite,
    NormalizedRecord,
    SourceConfig,
    YearMonth,
)

# Add a module here when you add an adapter. Importing it must register one instance.
ADAPTER_MODULES: tuple[str, ...] = (
    "ubc.adapters.example_html",
    "ubc.adapters.open_meteo_hdd",
)


@dataclass(frozen=True)
class CollectContext:
    """One collection call: a city, a month, and either a tariff source or a listing site.

    Tariff, FX, and degree-day adapters receive a ``SourceConfig``. Listing
    adapters receive a ``ListingSite`` and must not invent a ``SourceConfig``
    (listings have no kind, reliability, or priority). Both kinds of adapter
    implement this same ``collect`` and register in the same registry.
    ``period.first_day`` is the 1st of the month being collected.
    """

    city: CityConfig
    period: YearMonth
    fetcher: ContentFetcher
    source: SourceConfig | ListingSite


def require_source(ctx: CollectContext) -> SourceConfig:
    """The tariff/FX/degree-day config. Raises if this call is for a listing site."""

    if isinstance(ctx.source, SourceConfig):
        return ctx.source
    raise TypeError(
        f"adapter {ctx.source.adapter!r} was given a listing site; "
        "tariff adapters read SourceConfig via require_source"
    )


def require_listing(ctx: CollectContext) -> ListingSite:
    """The listing-site config. Raises if this call is for a tariff source."""

    if isinstance(ctx.source, ListingSite):
        return ctx.source
    raise TypeError(
        f"adapter {ctx.source.adapter!r} was given a tariff source; "
        "listing adapters read ListingSite via require_listing"
    )


@dataclass
class AdapterOutput:
    """Tariff adapters fill ``records``. Listing adapters fill ``candidates``."""

    records: list[NormalizedRecord] = field(default_factory=list)
    candidates: list[BuildingCandidate] = field(default_factory=list)


class Adapter(Protocol):
    """One source or listing site. ``name`` matches the config's ``adapter`` field."""

    name: str

    def collect(self, ctx: CollectContext) -> AdapterOutput: ...


class Registry:
    """Adapters keyed by name. A second different adapter with the same name fails."""

    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}

    def register(self, adapter: Adapter) -> None:
        if not adapter.name:
            raise ValueError("adapter name is empty")
        existing = self._adapters.get(adapter.name)
        if existing is adapter:
            return
        if existing is not None:
            raise ValueError(f"adapter {adapter.name!r} is already registered")
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> Adapter:
        try:
            return self._adapters[name]
        except KeyError:
            known = ", ".join(sorted(self._adapters)) or "(none)"
            raise KeyError(f"unknown adapter {name!r}; registered: {known}") from None

    def names(self) -> list[str]:
        return sorted(self._adapters)


default_registry = Registry()
_loaded = False


def register_adapter(adapter: Adapter) -> Adapter:
    """Register ``adapter`` on the process-wide registry. Returns it for chaining."""

    default_registry.register(adapter)
    return adapter


def load_adapters() -> Registry:
    """Import every module in ``ADAPTER_MODULES``. Safe to call more than once."""

    global _loaded
    if not _loaded:
        for module_name in ADAPTER_MODULES:
            importlib.import_module(module_name)
        _loaded = True
    return default_registry


def get_adapter(name: str) -> Adapter:
    """Return the adapter configured under ``name``, loading modules on first use."""

    load_adapters()
    return default_registry.get(name)
