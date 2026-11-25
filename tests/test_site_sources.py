"""The «Источники» tab renders methodology.json and loads it only when it is opened."""

from __future__ import annotations

import json
import subprocess

from ubc.methodology import build_methodology
from ubc.paths import REPO_ROOT, data_dir, example_city_month_path


def _node(script: str, payload: object = None) -> str:
    completed = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload).encode(),
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    return completed.stdout.decode()


def _render(overrides: dict, snapshots: list[dict] | None = None) -> str:
    script = """
const fs = require("fs");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
require("./site/sources.js");
require("./site/app.js");
process.stdout.write(globalThis.UbcSite.render(payload.snapshots, payload.overrides));
"""
    return _node(script, {"snapshots": snapshots or [], "overrides": overrides})


def _source(title: str, url: str | None, **extra: object) -> dict:
    return {
        "title": title,
        "url": url,
        "kind": "официальный",
        "enabled": True,
        "reading": "таблица в PDF",
        **extra,
    }


def _methodology(**city: object) -> dict:
    return {
        "schema_version": 1,
        "repo_url": "https://example.test/repo",
        "sections": [
            {
                "id": "calculation",
                "title": "Как считаем",
                "lead": "тариф × норма",
                "items": ["шаг"],
            },
            {"id": "limits", "title": "Что не учтено", "lead": "расчёт", "items": ["общее"]},
        ],
        "marks": [{"label": "по норме расхода", "text": "не счётчик"}],
        "cities": [
            {
                "id": "yerevan",
                "name": "Ереван",
                "notice": None,
                "articles": [
                    {
                        "item": "gas",
                        "label": "Газ",
                        "sources": [_source("Регулятор", "https://example.test/gas")],
                        "note": None,
                    },
                    {"item": "heating", "label": "Отопление", "sources": [], "note": "см. газ"},
                ],
                "exchange": {
                    "sources": [_source("ЦБ", "https://example.test/fx")],
                    "note": None,
                },
                "weather": [_source("Погода", "https://example.test/weather")],
                "houses": {
                    "sites": [_source("Объявления", "https://example.test/ads", reading=None)],
                    "open_data": ["карта: координаты"],
                    "classes": [{"label": "Бизнес-класс", "reasons": ["дорого"]}],
                    "heatings": [{"label": "Котёл", "reasons": ["газ"]}],
                    "price_note": "пороги — договорённость",
                    "prices": [{"districts": "Center", "price": "1 USD/м²", "source": "стат"}],
                    "unavailable": [{"label": "Центральное", "text": "нет тарифа"}],
                },
                "norms": [
                    {
                        "label": "Газ",
                        "heating": "Котёл",
                        "rows": [
                            {
                                "classes": "Панель",
                                "value": "22,5 м³ на м² в год",
                                "flat": "1350 м³ в год на эталонную квартиру",
                                "source": "отчёт",
                                "note": "подробности",
                            }
                        ],
                    }
                ],
                "limits": ["городское"],
                **city,
            }
        ],
    }


def test_the_grid_page_links_to_the_sources_tab() -> None:
    example = json.loads(example_city_month_path().read_text(encoding="utf-8"))
    html = _render({}, [{**example, "example": True}])

    assert '<a class="page-link" data-page="sources" href="#sources">Источники' in html
    assert 'data-page="grid" href="#" aria-current="page"' in html
    assert "ПРИМЕР ДАННЫХ" in html
    assert 'class="grid"' in html


def test_the_tab_shows_sources_calculation_and_limits_of_the_selected_city() -> None:
    example = json.loads(example_city_month_path().read_text(encoding="utf-8"))
    html = _render(
        {"city": "yerevan", "sources": {"methodology": _methodology()}},
        [{**example, "example": True}],
    )

    assert 'data-page="sources" href="#sources" aria-current="page"' in html
    assert 'data-city="yerevan" aria-selected="true"' in html
    assert "Откуда данные: Ереван" in html
    for url in ("gas", "fx", "weather", "ads", "repo"):
        assert (
            f'<a href="https://example.test/{url}" target="_blank" rel="noopener noreferrer">'
            in html
        )
    for text in (
        "Регулятор",
        "официальный · таблица в PDF",
        "см. газ",
        "пороги — договорённость",
        "дорого",
        "нет тарифа",
        "22,5 м³ на м² в год",
        "1350 м³ в год на эталонную квартиру",
        "подробности",
        "тариф × норма",
        "не счётчик",
        "общее",
        "городское",
    ):
        assert text in html
    assert html.index("Откуда данные") < html.index("Как считаем") < html.index("Что не учтено")
    assert "ПРИМЕР ДАННЫХ" not in html
    assert 'class="grid"' not in html


def test_a_disabled_source_is_marked() -> None:
    article = {
        "item": "water",
        "label": "Вода",
        "sources": [_source("Скан", None, enabled=False, kind="официальный, выключен")],
        "note": None,
    }
    html = _render({"sources": {"methodology": _methodology(articles=[article])}})

    assert 'class="src off"' in html
    assert "официальный, выключен" in html


def test_an_unconnected_city_says_so_instead_of_listing_articles() -> None:
    city = _methodology(notice="Тарифы этого города ещё не подключены")
    city["cities"][0]["houses"]["sites"] = []
    html = _render({"sources": {"methodology": city}})

    assert "Тарифы этого города ещё не подключены" in html
    assert "Регулятор" not in html
    assert "Сайты объявлений не подключены" in html
    assert "https://example.test/fx" in html
    assert "22,5 м³ на м² в год" in html


def test_text_is_escaped_and_only_http_links_become_anchors() -> None:
    city = _methodology(
        articles=[
            {
                "item": "gas",
                "label": "Газ",
                "sources": [
                    _source('<img src=x onerror="a">', "javascript:alert(1)"),
                    _source("Кавычка", 'https://example.test/"><script>'),
                ],
                "note": None,
            }
        ],
        limits=["<script>alert(1)</script>, см. (https://example.test/doc/(C)/x)."],
    )
    html = _render({"sources": {"methodology": city}})

    assert "<img" not in html
    assert "<script>" not in html
    assert "&lt;img src=x" in html
    assert "javascript:" not in html
    assert 'href="https://example.test/&quot;&gt;&lt;script&gt;"' in html
    assert 'href="https://example.test/doc/(C)/x" target' in html


def test_loading_and_failure_are_shown_in_the_tab() -> None:
    assert "Загрузка…" in _render({"sources": {}})
    failed = _render({"sources": {"error": "не вышло <b>"}})
    assert "не вышло &lt;b&gt;" in failed
    assert 'role="alert"' in failed


def test_every_real_city_renders() -> None:
    methodology = build_methodology(data_dir())
    for city in methodology["cities"]:
        html = _render({"city": city["id"], "sources": {"methodology": methodology}})
        assert f"Откуда данные: {city['name']}" in html
        assert "undefined" not in html
        assert "null" not in html


_MOUNT = """
const fs = require("fs");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
require("./site/sources.js");
require("./site/app.js");
const calls = [];
const listeners = {};
const host = {
  location: { hash: payload.hash },
  addEventListener(name, listener) { listeners[name] = listener; },
  scrollTo() {},
};
const container = {
  innerHTML: "",
  contains() { return false; },
  querySelectorAll() { return []; },
  querySelector() { return null; },
};
async function fetchImpl(url) {
  calls.push(url);
  if (url === "data/site-index.json") return { ok: true, async json() { return { files: [] }; } };
  return { ok: payload.ok, async json() { return payload.methodology; } };
}
const settle = async () => { for (let i = 0; i < 20; i += 1) await null; };
(async () => {
  globalThis.UbcSite.mount(container, { fetch: fetchImpl, window: host });
  await settle();
  const before = calls.slice();
  const closed = container.innerHTML;
  host.location.hash = "#sources";
  listeners.hashchange();
  await settle();
  listeners.hashchange();
  await settle();
  process.stdout.write(JSON.stringify({ before, closed, calls, open: container.innerHTML }));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


def test_the_document_is_fetched_once_and_only_when_the_tab_opens() -> None:
    seen = json.loads(_node(_MOUNT, {"hash": "", "ok": True, "methodology": _methodology()}))

    assert seen["before"] == ["data/site-index.json"]
    assert "Откуда данные" not in seen["closed"]
    assert seen["calls"] == ["data/site-index.json", "data/methodology.json"]
    assert "Откуда данные: Ереван" in seen["open"]


def test_a_deep_link_opens_the_tab_at_once() -> None:
    seen = json.loads(
        _node(_MOUNT, {"hash": "#sources", "ok": True, "methodology": _methodology()})
    )

    assert "Откуда данные: Ереван" in seen["closed"]


def test_a_missing_document_does_not_break_the_page() -> None:
    seen = json.loads(_node(_MOUNT, {"hash": "", "ok": False, "methodology": None}))

    assert "Нет посчитанных месяцев." in seen["closed"]
    assert "Не удалось загрузить описание источников" in seen["open"]
    assert "python -m ubc.methodology" in seen["open"]


def test_the_page_loads_the_tab_script_without_external_addresses() -> None:
    html = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "site" / "sources.js").read_text(encoding="utf-8")

    assert html.index('src="site/sources.js"') < html.index("UbcSite.mount")
    assert "https://" not in script
    assert "http://" not in script
