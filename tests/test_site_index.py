"""Which CityMonth files the static site is allowed to load."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ubc.load import ConfigError
from ubc.paths import data_dir, example_city_month_path
from ubc.site_index import build_site_index


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _snapshot(city: str, month: str) -> dict:
    document = json.loads(example_city_month_path().read_text(encoding="utf-8"))
    document["city"] = city
    document["month"] = month
    document["updated_at"] = f"{month}-15T00:00:00+00:00"
    document["exchange_rate"]["month"] = month
    currency = {
        "yerevan": "AMD",
        "tbilisi": "GEL",
        "belgrade": "RSD",
        "warsaw": "PLN",
        "lisbon": "EUR",
        "berlin": "EUR",
    }[city]
    document["exchange_rate"]["currency"] = currency
    document["exchange_rate"]["per_eur"] = 1 if currency == "EUR" else 10
    return document


def test_index_lists_examples_and_published_months_skipping_other_json(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write(data / "cities" / "yerevan.json", {"id": "yerevan"})
    _write(data / "reference.json", {"schema_version": 1})
    _write(data / "site-index.json", {"files": []})
    _write(data / "notes.json", {"note": "not a snapshot"})
    _write(data / "examples" / "yerevan-2026-10.json", _snapshot("yerevan", "2026-10"))
    _write(data / "examples" / "belgrade-2026-09.json", _snapshot("belgrade", "2026-09"))
    _write(data / "yerevan-2026-10.json", _snapshot("yerevan", "2026-10"))
    _write(data / "belgrade-2026-11.json", _snapshot("belgrade", "2026-11"))

    assert build_site_index(data) == {
        "schema_version": 1,
        "files": [
            {"path": "yerevan-2026-10.json", "example": False},
            {"path": "examples/belgrade-2026-09.json", "example": True},
            {"path": "belgrade-2026-11.json", "example": False},
        ],
    }


def test_index_rejects_a_misnamed_or_mismatched_snapshot(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write(data / "examples" / "readme.json", _snapshot("yerevan", "2026-10"))

    with pytest.raises(ConfigError, match="readme.json"):
        build_site_index(data)

    data = tmp_path / "other"
    _write(data / "yerevan-2026-10.json", _snapshot("belgrade", "2026-10"))

    with pytest.raises(ConfigError, match="yerevan-2026-10.json"):
        build_site_index(data)


def test_repository_index_matches_the_files_next_to_it() -> None:
    index = build_site_index(data_dir())

    assert "yerevan-2026-10.json" in {Path(file["path"]).name for file in index["files"]}
    on_disk = json.loads((data_dir() / "site-index.json").read_text(encoding="utf-8"))
    assert on_disk == index
