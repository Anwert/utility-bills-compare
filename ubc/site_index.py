"""Index of CityMonth JSON files the static site loads.

The site is plain HTML and cannot list a directory. ``data/site-index.json``
names every snapshot to fetch:

- ``data/examples/<city>-<YYYY-MM>.json`` — contract fixtures (``example: true``)
- ``data/<city>-<YYYY-MM>.json`` — files a later pipeline writes

A published file replaces the example for the same city and month.
``reference.json``, ``site-index.json``, and ``data/cities/*.json`` are not
snapshots. Rebuild with ``python -m ubc.site_index``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ubc.load import ConfigError
from ubc.paths import data_dir
from ubc.schemas import CityId, CityMonth

_CITY_ORDER = [city.value for city in CityId]
_SNAPSHOT_NAME = re.compile(rf"^({'|'.join(_CITY_ORDER)})-(\d{{4}}-\d{{2}})\.json$")


def build_site_index(data_root: Path) -> dict[str, object]:
    """Return the site index for ``data_root`` without writing it."""

    chosen: dict[tuple[str, str], dict[str, object]] = {}
    examples = data_root / "examples"
    if examples.is_dir():
        for path in sorted(examples.glob("*.json")):
            city, month = _read_snapshot(path)
            chosen[(city, month)] = {
                "path": path.relative_to(data_root).as_posix(),
                "example": True,
            }
    for path in sorted(data_root.glob("*.json")):
        match = _SNAPSHOT_NAME.fullmatch(path.name)
        if match is None:
            continue
        city, month = _read_snapshot(path)
        chosen[(city, month)] = {
            "path": path.relative_to(data_root).as_posix(),
            "example": False,
        }
    files = [
        chosen[key]
        for key in sorted(chosen, key=lambda item: (_CITY_ORDER.index(item[0]), item[1]))
    ]
    return {"schema_version": 1, "files": files}


def write_site_index(data_root: Path | None = None) -> Path:
    """Write ``data/site-index.json`` and return its path."""

    root = data_dir() if data_root is None else data_root
    target = root / "site-index.json"
    payload = build_site_index(root)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _read_snapshot(path: Path) -> tuple[str, str]:
    match = _SNAPSHOT_NAME.fullmatch(path.name)
    if match is None:
        raise ConfigError(f"{path.name}: file name must be <city>-<YYYY-MM>.json")
    city, month = match.group(1), match.group(2)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    try:
        snapshot = CityMonth.model_validate_json(text)
    except Exception as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    if snapshot.city.value != city or snapshot.month != month:
        raise ConfigError(
            f"{path.name}: file name is {city} {month}, "
            f"document is {snapshot.city.value} {snapshot.month}"
        )
    return city, month


def main() -> int:
    path = write_site_index()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
