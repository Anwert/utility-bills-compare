"""Load the JSON documents under ``data/`` and reject invalid ones."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from ubc.paths import cities_dir, example_city_month_path, reference_path
from ubc.schemas import CityConfig, CityMonth, ReferenceData


class ConfigError(Exception):
    """A data file is missing or does not match its schema."""


def read_document[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        raise ConfigError(f"{path}:\n{exc}") from exc


def load_city(city_id: str) -> CityConfig:
    path = cities_dir() / f"{city_id}.json"
    if not path.is_file():
        known = ", ".join(sorted(item.stem for item in cities_dir().glob("*.json")))
        raise ConfigError(f"unknown city {city_id!r}; known: {known}")
    city = read_document(path, CityConfig)
    if city.id.value != city_id:
        raise ConfigError(f"{path}: id is {city.id.value}, file name is {city_id}")
    return city


def load_cities() -> list[CityConfig]:
    """The six city files, sorted by id."""

    return [load_city(path.stem) for path in sorted(cities_dir().glob("*.json"))]


def load_reference() -> ReferenceData:
    return read_document(reference_path(), ReferenceData)


def load_example_city_month() -> CityMonth:
    """Illustrative site JSON. Numbers are not measured tariffs."""

    return read_document(example_city_month_path(), CityMonth)
