"""The static page renders CityMonth JSON: grid, empty cells, and breakdown."""

from __future__ import annotations

import json
import subprocess

from ubc.paths import REPO_ROOT, example_city_month_path
from ubc.schemas import CityMonth

_CLASSES = ("soviet_panel", "typical_newbuild", "business")
_HEATS = ("district", "gas_boiler", "electric")
_CURRENCY = {
    "yerevan": "AMD",
    "tbilisi": "GEL",
    "belgrade": "RSD",
    "warsaw": "PLN",
    "lisbon": "EUR",
    "berlin": "EUR",
}


def _render(snapshots: list[dict], overrides: dict | None = None) -> str:
    payload = json.dumps({"snapshots": snapshots, "overrides": overrides or {}})
    script = """
const fs = require("fs");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
require("./site/app.js");
process.stdout.write(globalThis.UbcSite.render(payload.snapshots, payload.overrides));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        input=payload.encode(),
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    return completed.stdout.decode()


def _run_node(script: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )


def _line(
    item: str,
    category: str,
    amount: float,
    url: str,
    kind: str,
    valid_from: str,
    marks: list[str],
    note: str = "",
) -> dict:
    return {
        "item": item,
        "category": category,
        "amount_eur": amount,
        "source_url": url,
        "source_kind": kind,
        "valid_from": valid_from,
        "marks": marks,
        "note": note,
    }


def _cells(
    lines: list[dict],
    address: str,
    url: str,
    empty: dict[tuple[str, str], str],
) -> list[dict]:
    housing = sum(line["amount_eur"] for line in lines if line["category"] == "housing")
    utility = sum(line["amount_eur"] for line in lines if line["category"] == "utility")
    cells = []
    for house_class in _CLASSES:
        for heating in _HEATS:
            reason = empty.get((house_class, heating))
            if reason is not None:
                cells.append(
                    {
                        "house_class": house_class,
                        "heating": heating,
                        "status": "empty",
                        "empty_reason": reason,
                        "total_eur": None,
                        "housing_eur": None,
                        "utility_eur": None,
                        "lines": [],
                        "building": None,
                    }
                )
                continue
            cells.append(
                {
                    "house_class": house_class,
                    "heating": heating,
                    "status": "ok",
                    "empty_reason": None,
                    "total_eur": housing + utility,
                    "housing_eur": housing,
                    "utility_eur": utility,
                    "lines": lines,
                    "building": {"id": f"{house_class}-{heating}", "address": address, "url": url},
                }
            )
    return cells


def _document(
    city: str,
    month: str,
    *,
    per_eur: float,
    source_name: str,
    source_url: str,
    paid_data_note: str,
    updated_at: str,
    lines: list[dict],
    address: str,
    listing_url: str,
    empty: dict[tuple[str, str], str],
) -> dict:
    document = {
        "schema_version": 1,
        "city": city,
        "month": month,
        "updated_at": updated_at,
        "apartment": {"rooms": 2, "area_m2": 60, "occupants": 2},
        "exchange_rate": {
            "currency": _CURRENCY[city],
            "per_eur": per_eur,
            "month": month,
            "source_url": source_url,
            "source_name": source_name,
        },
        "paid_data_note": paid_data_note,
        "cells": _cells(lines, address, listing_url, empty),
    }
    CityMonth.model_validate(document)
    return document


def _amounts(document: dict, housing: int, utility: int, total: int) -> dict:
    if housing + utility != total:
        raise AssertionError("fixture totals are inconsistent")
    for cell in document["cells"]:
        if cell["status"] != "ok":
            continue
        cell["housing_eur"] = housing
        cell["utility_eur"] = utility
        cell["total_eur"] = total
        cell["lines"][0]["amount_eur"] = housing
        cell["lines"][1]["amount_eur"] = utility
    CityMonth.model_validate(document)
    return document


def test_repository_fixture_renders_the_grid_breakdown_and_example_banner() -> None:
    script = """
const fs = require("fs");
const path = require("path");
require("./site/app.js");
(async () => {
  const root = process.cwd();
  // Only the example: the published months in data/ change every month.
  const index = {
    schema_version: 1,
    files: [{ path: "examples/yerevan-2026-10.json", example: true }],
  };
  async function fetchImpl(url) {
    if (url === "data/site-index.json") {
      return { ok: true, async json() { return index; } };
    }
    try {
      const text = fs.readFileSync(path.join(root, url), "utf8");
      return { ok: true, async json() { return JSON.parse(text); } };
    } catch (err) {
      return { ok: false, async json() { throw err; } };
    }
  }
  const snapshots = await globalThis.UbcSite.loadIndex(fetchImpl, { prefix: "data/" });
  process.stdout.write(globalThis.UbcSite.render(snapshots, {}));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr.decode()
    html = completed.stdout.decode()

    assert "Ереван" in html
    assert "Тбилиси" in html
    assert "Берлин" in html
    assert "ПРИМЕР ДАННЫХ — цифры выдуманы" in html
    assert "Октябрь 2026" in html
    assert 'value="2026-10" selected' in html
    assert "2 комнаты, 60 м², 2 жильца" in html
    assert "Обновлено 24.11.2026" in html
    assert 'class="cell empty"' in html
    assert "дом такого класса с таким отоплением автоматически не найден" in html
    assert "€30,00" in html
    assert "жил. €10,00" in html
    assert "ком. €20,00" in html
    assert 'class="mark">фиксированный платёж' in html
    assert 'class="mark">по норме расхода' in html
    assert 'class="mark">оценка по статистике' in html
    assert html.count("точные данные поставщика возможны за доплату") == 1
    assert 'href="https://example.test/electricity"' in html
    assert 'href="https://example.test/maintenance"' in html
    assert "тариф с 01.01.2026" in html
    assert "пример, не реальный дом" in html
    assert 'href="https://example.test/listing/soviet_panel-gas_boiler"' in html
    assert "400 AMD за 1 €" in html
    assert 'href="https://example.test/fx"' in html
    assert html.index("Жилищные услуги") < html.index("Коммунальные услуги")
    assert "Итого жилищные" in html
    assert "Итого коммунальные" in html


def test_page_renders_every_city_and_month_with_grey_empty_cells() -> None:
    october = json.loads(example_city_month_path().read_text(encoding="utf-8"))
    december = json.loads(example_city_month_path().read_text(encoding="utf-8"))
    december["month"] = "2026-12"
    december["updated_at"] = "2026-12-02T08:00:00+00:00"
    december["exchange_rate"]["month"] = "2026-12"
    _amounts(december, housing=15, utility=26, total=41)
    CityMonth.model_validate(october)

    belgrade_lines = [
        _line(
            "maintenance",
            "housing",
            15,
            "https://example.test/hoa",
            "official",
            "2026-02-01",
            ["fixed"],
        ),
        _line(
            "heating",
            "utility",
            20,
            "https://example.test/heat",
            "third_party",
            "2026-03-01",
            ["third_party", "stale"],
        ),
        _line(
            "electricity",
            "utility",
            6,
            "https://example.test/power",
            "statistics",
            "2026-04-01",
            ["llm_extracted", "norm_based"],
        ),
    ]
    belgrade = _document(
        "belgrade",
        "2026-08",
        per_eur=117.45,
        source_name="Eurostat",
        source_url="https://example.test/rsd",
        paid_data_note="белград: точные данные возможны за доплату",
        updated_at="2026-08-20T12:00:00+00:00",
        lines=belgrade_lines,
        address="Белград, улица Пример 1",
        listing_url="https://example.test/listing/belgrade",
        empty={("business", "electric"): "нет такого дома в Белграде"},
    )
    lisbon = _document(
        "lisbon",
        "2026-05",
        per_eur=1,
        source_name="евро",
        source_url="https://example.test/eur",
        paid_data_note="лиссабонская пометка",
        updated_at="2026-05-02T00:00:00+00:00",
        lines=[
            _line(
                "maintenance",
                "housing",
                8,
                "https://example.test/condo",
                "official",
                "2026-01-01",
                ["fixed"],
                note="<script>alert(1)</script>",
            ),
            _line(
                "electricity",
                "utility",
                9,
                "https://example.test/luz",
                "official",
                "2026-01-01",
                ["fixed"],
            ),
        ],
        address="Лиссабон, пример",
        listing_url="https://example.test/listing/lisbon",
        empty={("soviet_panel", "district"): "пусто"},
    )
    snapshots = [
        {**october, "example": True},
        {**december, "example": False},
        {**belgrade, "example": False},
        {**lisbon, "example": False},
    ]

    latest = _render(snapshots, {})
    assert 'data-city="yerevan" aria-selected="true"' in latest
    assert 'value="2026-12" selected' in latest
    assert "Декабрь 2026" in latest
    assert "Октябрь 2026" in latest
    assert "ПРИМЕР ДАННЫХ" not in latest
    assert "€41,00" in latest
    assert "жил. €15,00" in latest
    assert "ком. €26,00" in latest
    assert "Обновлено 02.12.2026" in latest

    clamped = _render(snapshots, {"city": "yerevan", "month": "1999-01"})
    assert 'value="2026-12" selected' in clamped

    october_html = _render(
        snapshots,
        {
            "city": "yerevan",
            "month": "2026-10",
            "houseClass": "soviet_panel",
            "heating": "district",
        },
    )
    assert "ПРИМЕР ДАННЫХ — цифры выдуманы" in october_html
    assert 'class="cell empty selected"' in october_html
    assert "дом такого класса с таким отоплением автоматически не найден" in october_html
    assert "Итого жилищные" not in october_html

    filled = _render(
        snapshots,
        {
            "city": "yerevan",
            "month": "2026-10",
            "houseClass": "soviet_panel",
            "heating": "gas_boiler",
        },
    )
    assert "€30,00" in filled
    assert "Итого жилищные" in filled
    assert "€10,00" in filled

    missing = _render(snapshots, {"city": "tbilisi"})
    assert 'data-city="tbilisi" aria-selected="true"' in missing
    assert "За этот город ещё нет посчитанного месяца." in missing
    assert "€41" not in missing
    assert "€30" not in missing

    belgrade_html = _render(snapshots, {"city": "belgrade"})
    assert "Август 2026" in belgrade_html
    assert "Декабрь 2026" not in belgrade_html
    assert "€41,00" in belgrade_html
    assert "€20,00" in belgrade_html
    assert "€6,00" in belgrade_html
    assert 'class="mark">сторонний сайт' in belgrade_html
    assert 'class="mark">значение не обновилось' in belgrade_html
    assert 'class="mark">извлечено моделью' in belgrade_html
    assert belgrade_html.count("белград: точные данные возможны за доплату") == 2
    assert "тариф с 01.02.2026" in belgrade_html
    assert "тариф с 01.03.2026" in belgrade_html
    assert "тариф с 01.04.2026" in belgrade_html
    assert 'href="https://example.test/heat"' in belgrade_html
    assert 'href="https://example.test/power"' in belgrade_html
    assert 'href="https://example.test/hoa"' in belgrade_html
    assert "Белград, улица Пример 1" in belgrade_html
    assert 'href="https://example.test/listing/belgrade"' in belgrade_html
    assert "117,45 RSD за 1 €" in belgrade_html
    assert 'href="https://example.test/rsd"' in belgrade_html
    assert "Eurostat" in belgrade_html
    assert "Обновлено 20.08.2026" in belgrade_html
    assert "2 комнаты, 60 м², 2 жильца" in belgrade_html
    assert 'class="cell empty"' in belgrade_html
    assert "нет такого дома в Белграде" in belgrade_html
    assert belgrade_html.index("Жилищные услуги") < belgrade_html.index("Коммунальные услуги")

    empty_sheet = _render(
        snapshots,
        {"city": "belgrade", "houseClass": "business", "heating": "electric"},
    )
    assert "нет такого дома в Белграде" in empty_sheet
    assert "Итого жилищные" not in empty_sheet

    lisbon_html = _render(snapshots, {"city": "lisbon"})
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in lisbon_html
    assert "<script>" not in lisbon_html
    assert "лиссабонская пометка" not in lisbon_html

    none = _render([], {})
    assert "Нет посчитанных месяцев." in none
    assert "Ереван" in none
    assert "Берлин" in none


def _one_line(amount: float, marks: list[str], note: str = "") -> dict:
    return _document(
        "lisbon",
        "2026-05",
        per_eur=1,
        source_name="евро",
        source_url="https://example.test/eur",
        paid_data_note="точные данные за доплату",
        updated_at="2026-05-02T00:00:00+00:00",
        lines=[
            _line(
                "water",
                "utility",
                amount,
                "https://example.test/water",
                "statistics",
                "2026-01-01",
                marks,
                note=note,
            )
        ],
        address="Лиссабон, пример",
        listing_url="https://example.test/listing/lisbon",
        empty={},
    )


def test_money_is_always_shown_with_cents() -> None:
    for amount, shown in ((32.4, "€32,40"), (3.7, "€3,70"), (0, "€0,00"), (101.99, "€101,99")):
        html = _render([_one_line(amount, ["fixed"])])
        assert f"<b>{shown}</b>" in html
        assert f"<span>{shown}</span>" in html


def test_paid_data_sentence_already_in_the_note_is_not_repeated() -> None:
    html = _render([_one_line(5, ["statistics_estimate"], note="Оценка. точные данные за доплату")])

    assert html.count("точные данные за доплату") == 1


def test_load_index_rejects_a_path_outside_data() -> None:
    script = """
require("./site/app.js");
(async () => {
  async function fetchImpl(url) {
    if (url.endsWith("site-index.json")) {
      return {
        ok: true,
        async json() {
          return { files: [{ path: "../reference.json", example: false }] };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  }
  await globalThis.UbcSite.loadIndex(fetchImpl, { prefix: "data/" });
})().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
"""
    completed = _run_node(script)
    assert completed.returncode == 1
    assert "Некорректный путь" in completed.stderr.decode()


def test_index_is_static_russian_and_usable_on_a_phone() -> None:
    html = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
    css = (REPO_ROOT / "site" / "app.css").read_text(encoding="utf-8")
    script = (REPO_ROOT / "site" / "app.js").read_text(encoding="utf-8")

    assert '<html lang="ru">' in html
    assert "width=device-width, initial-scale=1" in html
    assert 'src="site/app.js"' in html
    assert 'href="site/app.css"' in html
    assert "UbcSite.mount" in html
    assert "https://" not in html
    assert "http://" not in html
    assert "https://" not in script
    assert "http://" not in script
    assert ".cell.empty" in css
    assert "#f3f4f6" in css
    assert "grid-template-columns" in css
    assert "1200px" not in css
    assert "url(http" not in css
    head = html.split("</head>", 1)[0]
    assert "<link" in head
    assert head.count("<link") == 1
