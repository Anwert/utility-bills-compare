"""Locations of repository data. Paths are relative to this checkout."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    return REPO_ROOT / "data"


def cities_dir() -> Path:
    return data_dir() / "cities"


def reference_path() -> Path:
    return data_dir() / "reference.json"


def example_city_month_path() -> Path:
    return data_dir() / "examples" / "yerevan-2026-10.json"
