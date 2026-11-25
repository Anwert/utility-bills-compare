"""The directory GitHub Pages serves.

Only what the page loads: ``index.html``, ``site/``, the index of months, the
months it names, the rates it converts euros with, and the generated
``methodology.json`` of the «Источники» tab.
Raw answers, policy state, tests, and Python stay in the repository.

    python -m ubc.site_bundle _site
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from ubc.display_rates import FILE_NAME, load_display_rates
from ubc.load import ConfigError
from ubc.methodology import write_methodology
from ubc.paths import REPO_ROOT


def build_site_bundle(repo_root: Path, out: Path) -> Path:
    """Fill the empty or missing directory ``out`` and return it."""

    if out.exists() and any(out.iterdir()):
        raise ConfigError(f"{out} is not empty")
    data = repo_root / "data"
    index = json.loads((data / "site-index.json").read_text(encoding="utf-8"))
    names = ["site-index.json", *(str(file["path"]) for file in index["files"])]
    for name in names:
        if not (data / name).is_file():
            raise ConfigError(f"data/site-index.json names a missing file {name}")
    load_display_rates(data)
    if (data / FILE_NAME).is_file():
        names.append(FILE_NAME)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", ".nojekyll"):
        shutil.copyfile(repo_root / name, out / name)
    shutil.copytree(repo_root / "site", out / "site")
    for name in names:
        target = out / "data" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data / name, target)
    write_methodology(data, out / "data" / "methodology.json")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="Directory to create.")
    args = parser.parse_args(argv)
    try:
        print(build_site_bundle(REPO_ROOT, args.out))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
