"""What one month leaves in the repository for the next one.

``data/state/<city>.json`` — policy state and every value that was ever in force.
``data/houses/<city>.json`` — pinned houses and the reasons of the empty cells.

A changed tariff is appended to ``history``; the old one keeps its own dates, so
a recount of a past month still finds it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ubc.load import ConfigError, read_document
from ubc.policy import ActingValue, load_acting
from ubc.schemas import (
    CHARGE_ITEMS,
    SCHEMA_VERSION,
    CityId,
    ContractModel,
    HeatingType,
    HouseClass,
    Item,
    NormalizedRecord,
    PinnedHouse,
    YearMonth,
)


class HistoryEntry(ContractModel):
    """Everything one source published for an article, from the month it was first seen."""

    period: str
    source_id: str
    records: list[NormalizedRecord] = Field(min_length=1)

    @field_validator("period")
    @classmethod
    def _month(cls, value: str) -> str:
        return str(YearMonth.parse(value))


class ArticleState(ContractModel):
    """``acting`` is ``dump_acting``; ``checked`` is the month it was last decided for."""

    item: Item
    acting: dict[str, Any]
    checked: str
    stale: bool
    note: str = ""
    history: list[HistoryEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_article(self) -> ArticleState:
        if self.item not in CHARGE_ITEMS:
            raise ValueError("state keeps bill articles only")
        YearMonth.parse(self.checked)
        try:
            load_acting(self.acting)
        except KeyError as exc:
            raise ValueError(f"acting value has no {exc}") from exc
        return self

    def acting_value(self) -> ActingValue:
        acting = load_acting(self.acting)
        if "others" in self.acting:
            return acting
        # Saved before the policy compared them: the last published entry has them all.
        return replace(acting, others=tuple(self.history[-1].records[1:]))

    def checked_month(self) -> YearMonth:
        return YearMonth.parse(self.checked)


class CityState(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    city: CityId
    articles: list[ArticleState] = Field(default_factory=list)

    def article(self, item: Item) -> ArticleState | None:
        return next((article for article in self.articles if article.item is item), None)


class EmptyCell(ContractModel):
    house_class: HouseClass
    heating: HeatingType
    reason: str = Field(min_length=1)


class CityHouses(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    city: CityId
    period: str
    houses: list[PinnedHouse] = Field(default_factory=list)
    empty: list[EmptyCell] = Field(default_factory=list)

    @field_validator("period")
    @classmethod
    def _month(cls, value: str) -> str:
        return str(YearMonth.parse(value))

    def month(self) -> YearMonth:
        return YearMonth.parse(self.period)


def state_path(data_root: Path, city: CityId) -> Path:
    return data_root / "state" / f"{city.value}.json"


def houses_path(data_root: Path, city: CityId) -> Path:
    return data_root / "houses" / f"{city.value}.json"


def load_state(data_root: Path, city: CityId) -> CityState:
    path = state_path(data_root, city)
    if not path.is_file():
        return CityState(city=city)
    state = read_document(path, CityState)
    _require_city(path, city, state.city)
    return state


def load_houses(data_root: Path, city: CityId) -> CityHouses | None:
    path = houses_path(data_root, city)
    if not path.is_file():
        return None
    houses = read_document(path, CityHouses)
    _require_city(path, city, houses.city)
    return houses


def _require_city(path: Path, expected: CityId, found: CityId) -> None:
    """A file of another city would start the history and the pins over."""

    if found is not expected:
        raise ConfigError(f"{path}: file is for {expected.value}, document is {found.value}")
