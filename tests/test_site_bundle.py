"""What GitHub Pages serves: the site and the months it loads, nothing else."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ubc.load import ConfigError
from ubc.paths import REPO_ROOT
from ubc.site_bundle import build_site_bundle


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def test_bundle_is_the_page_its_assets_and_every_indexed_month(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site_bundle(REPO_ROOT, out)

    index = json.loads((REPO_ROOT / "data/site-index.json").read_text(encoding="utf-8"))
    assert _files(out) == {
        ".nojekyll",
        "index.html",
        "site/app.css",
        "site/app.js",
        "data/site-index.json",
        *(f"data/{file['path']}" for file in index["files"]),
    }


def test_bundle_refuses_an_index_that_names_a_missing_month(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "site").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "index.html").write_text("<html></html>", encoding="utf-8")
    (repo / ".nojekyll").touch()
    (repo / "data" / "site-index.json").write_text(
        json.dumps({"schema_version": 1, "files": [{"path": "yerevan-2026-10.json"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="yerevan-2026-10.json"):
        build_site_bundle(repo, tmp_path / "out")
