"""The «Источники» tab is generated from the city files and the reference norms."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from ubc.load import ConfigError, load_cities
from ubc.methodology import build_methodology, write_methodology
from ubc.paths import REPO_ROOT, data_dir
from ubc.schemas import MARK_LABELS_RU

_CITY_ORDER = ["yerevan", "tbilisi", "belgrade", "warsaw", "lisbon", "berlin"]


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """A private copy of the configuration that a test may edit."""

    root = tmp_path / "data"
    shutil.copytree(data_dir() / "cities", root / "cities")
    shutil.copyfile(data_dir() / "reference.json", root / "reference.json")
    return root


def _edit(path: Path, change) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _city(document: dict, city: str) -> dict:
    return next(item for item in document["cities"] if item["id"] == city)


def _article(city: dict, item: str) -> dict:
    return next(article for article in city["articles"] if article["item"] == item)


def _source(source_id: str, **extra: object) -> dict:
    return {
        "id": source_id,
        "adapter": "example_html",
        "kind": "official",
        "reliability": "official",
        **extra,
    }


def _set_gas(data_root: Path, *sources: dict) -> dict:
    def change(document: dict) -> None:
        for article in document["articles"]:
            if article["item"] == "gas":
                article["sources"] = list(sources)

    _edit(data_root / "cities" / "yerevan.json", change)
    return _article(_city(build_methodology(data_root), "yerevan"), "gas")


def test_every_configured_source_and_listing_site_has_a_human_title() -> None:
    for city in load_cities():
        sources = [source for article in city.articles for source in article.sources]
        for source in [*sources, *city.fx_sources, *city.hdd_sources]:
            assert source.title_ru, f"{city.id.value}: {source.id}"
            assert source.reading, f"{city.id.value}: {source.id}"
        for site in city.listing_sites:
            assert site.title_ru, f"{city.id.value}: {site.id}"


def test_article_source_shows_title_link_kind_and_how_it_is_read(data_root: Path) -> None:
    gas = _set_gas(
        data_root,
        _source(
            "a",
            title_ru="КРОУ: цены на газ",
            reading="pdf",
            settings={"url": "https://example.test/gas"},
        ),
    )

    assert gas["label"] == "Газ"
    assert gas["sources"] == [
        {
            "title": "КРОУ: цены на газ",
            "url": "https://example.test/gas",
            "kind": "официальный",
            "enabled": True,
            "reading": "таблица в PDF",
        }
    ]


def test_sources_follow_the_polling_order_and_disabled_ones_come_last(data_root: Path) -> None:
    gas = _set_gas(
        data_root,
        _source("off", title_ru="Скан", reading="scan", enabled=False),
        _source("s", title_ru="Ряд", kind="statistics", reliability="statistics"),
        _source("t", title_ru="Агрегатор", kind="third_party", reliability="third_party"),
        _source("o", title_ru="Регулятор", reading="api"),
    )

    assert [(source["title"], source["kind"]) for source in gas["sources"]] == [
        ("Регулятор", "официальный"),
        ("Агрегатор", "сторонний сайт"),
        ("Ряд", "статистика"),
        ("Скан", "официальный, выключен"),
    ]
    assert [source["enabled"] for source in gas["sources"]] == [True, True, True, False]


def test_an_untitled_source_falls_back_to_its_host_and_then_to_its_id(data_root: Path) -> None:
    gas = _set_gas(
        data_root,
        _source("with_url", settings={"url": "https://www.example.test/a/b"}),
        _source("bare", priority=1),
    )

    assert [(source["title"], source["url"]) for source in gas["sources"]] == [
        ("www.example.test", "https://www.example.test/a/b"),
        ("bare", None),
    ]
    assert gas["sources"][0]["reading"] is None


def test_human_page_replaces_an_api_endpoint_in_the_link(data_root: Path) -> None:
    gas = _set_gas(
        data_root,
        _source(
            "a",
            title_ru="API",
            page_url="https://example.test/about",
            settings={"url": "https://api.example.test/v1"},
        ),
    )

    assert gas["sources"][0]["url"] == "https://example.test/about"


def test_cities_come_yerevan_first_and_unconnected_ones_say_so() -> None:
    document = build_methodology(data_dir())

    assert [city["id"] for city in document["cities"]] == _CITY_ORDER
    assert _city(document, "yerevan")["notice"] is None
    for city in document["cities"]:
        connected = any(
            source["enabled"] for article in city["articles"] for source in article["sources"]
        )
        assert connected is (city["notice"] is None)


def test_a_city_whose_sources_are_all_off_is_not_connected(data_root: Path) -> None:
    def change(document: dict) -> None:
        for article in document["articles"]:
            for source in article["sources"]:
                source["enabled"] = False

    _edit(data_root / "cities" / "yerevan.json", change)

    notice = _city(build_methodology(data_root), "yerevan")["notice"]
    assert "ещё не подключены" in notice


def test_an_article_without_a_source_explains_itself(data_root: Path) -> None:
    def change(document: dict) -> None:
        for article in document["articles"]:
            if article["item"] == "heating":
                article["sources"] = []

    _edit(data_root / "cities" / "yerevan.json", change)
    yerevan = _city(build_methodology(data_root), "yerevan")

    heating = _article(yerevan, "heating")
    assert heating["sources"] == []
    assert "по цене газа" in heating["note"]
    assert _article(yerevan, "gas")["note"] is None


def test_exchange_rate_and_weather_are_listed_for_every_city() -> None:
    document = build_methodology(data_dir())

    for city in document["cities"]:
        assert [source["title"] for source in city["weather"]] == ["Open-Meteo: архив погоды ERA5"]
        assert city["weather"][0]["url"].startswith("https://open-meteo.com/")
    exchange = _city(document, "yerevan")["exchange"]
    assert exchange["sources"][0]["title"] == "Центральный банк Армении"
    assert exchange["note"] is None
    berlin = _city(document, "berlin")["exchange"]
    assert berlin["sources"] == []
    assert "евро" in berlin["note"]


def test_norms_are_grouped_and_written_in_russian_units(data_root: Path) -> None:
    def norm(house_class: str, value: float, **extra: object) -> dict:
        return {
            "city": "yerevan",
            "house_class": house_class,
            "item": "gas",
            "heating": "gas_boiler",
            "annual_value": value,
            "unit": "m3/m2/year",
            "basis": "per_m2",
            "source": "отчёт",
            "note": "примечание",
            **extra,
        }

    def change(document: dict) -> None:
        person = {"unit": "kWh/person/year", "basis": "per_person", "heating": None}
        document["norms"] = [
            norm("soviet_panel", 22.5),
            norm("typical_newbuild", 12.0),
            norm("business", 12.0),
            *(
                norm(row, 950, item="electricity", **person)
                for row in ("soviet_panel", "typical_newbuild", "business")
            ),
            norm(
                "business",
                131,
                unit="m3/person/year",
                basis="per_person",
                heating=None,
                applies_to=["gas_boiler"],
            ),
            norm("business", 80, item="heating", heating="electric", unit="kWh/m2/year"),
            norm("business", 1, city="tbilisi"),
        ]

    _edit(data_root / "reference.json", change)

    assert _city(build_methodology(data_root), "yerevan")["norms"] == [
        {
            "label": "Отопление",
            "scope": "столбец «Электрическое»",
            "rows": [
                {
                    "classes": "Бизнес-класс",
                    "value": "80 кВт·ч на м² в год",
                    "flat": "4800 кВт·ч в год на эталонную квартиру",
                    "source": "отчёт",
                    "note": "примечание",
                }
            ],
        },
        {
            "label": "Электричество",
            "scope": None,
            "rows": [
                {
                    "classes": "все классы домов",
                    "value": "950 кВт·ч на человека в год",
                    "flat": "1900 кВт·ч в год на эталонную квартиру",
                    "source": "отчёт",
                    "note": "примечание",
                }
            ],
        },
        {
            "label": "Газ",
            "scope": "на отопление, столбец «Индивидуальный газовый котёл»",
            "rows": [
                {
                    "classes": "Советский / панельный фонд",
                    "value": "22,5 м³ на м² в год",
                    "flat": "1350 м³ в год на эталонную квартиру",
                    "source": "отчёт",
                    "note": "примечание",
                },
                {
                    "classes": "Типовая новостройка 2000–2010-х; Бизнес-класс",
                    "value": "12 м³ на м² в год",
                    "flat": "720 м³ в год на эталонную квартиру",
                    "source": "отчёт",
                    "note": "примечание",
                },
            ],
        },
        {
            "label": "Газ",
            "scope": "кроме отопления, только столбец «Индивидуальный газовый котёл»",
            "rows": [
                {
                    "classes": "Бизнес-класс",
                    "value": "131 м³ на человека в год",
                    "flat": "262 м³ в год на эталонную квартиру",
                    "source": "отчёт",
                    "note": "примечание",
                }
            ],
        },
    ]


def test_house_rules_carry_reasons_and_call_their_thresholds_a_convention(
    data_root: Path,
) -> None:
    def rule(house_class: str, reason: str, **extra: object) -> dict:
        return {"house_class": house_class, "reason": reason, **extra}

    def price(district: str, amount: int) -> dict:
        return {"district": district, "amount": amount, "currency": "USD", "source": "статкомитет"}

    def change(document: dict) -> None:
        document["house_rules"] = {
            "classes": [
                rule("business", "дорогой монолит (решение #9): да", price_ratio_from=2.5),
                rule("business", "удобства", amenities_any=["gym", "concierge"]),
                rule("soviet_panel", "панель", building_types=["panel"]),
                rule("soviet_panel", "панель", building_types=["stone"]),
                rule(
                    "typical_newbuild",
                    "новый монолит",
                    price_ratio_to=1.6,
                    amenities_none=["gym"],
                ),
            ],
            "heatings": [
                {"heating": "gas_boiler", "all_of": ["a"], "reason": "свой котёл"},
                {"heating": "gas_boiler", "all_of": ["b"], "reason": "свой котёл"},
            ],
            "price_references": [price("Center", 2671), price("Avan", 1307), price("Awan", 1307)],
            "unavailable_heating": {"district": "тарифа нет"},
        }

    _edit(data_root / "cities" / "yerevan.json", change)
    document = build_methodology(data_root)
    houses = _city(document, "yerevan")["houses"]

    assert [site["title"] for site in houses["sites"]] == ["MyRealty.am", "Real-Estate.am"]
    assert houses["classes"] == [
        {"label": "Советский / панельный фонд", "reasons": ["панель"]},
        {"label": "Типовая новостройка 2000–2010-х", "reasons": ["новый монолит"]},
        {"label": "Бизнес-класс", "reasons": ["дорогой монолит: да", "удобства"]},
    ]
    assert houses["heatings"] == [
        {"label": "Индивидуальный газовый котёл", "reasons": ["свой котёл"]}
    ]
    assert houses["unavailable"] == [{"label": "Центральное", "text": "тарифа нет"}]
    assert houses["class_note"] == (
        "Бизнес-класс — цена м² не ниже 2,5 средней по району. "
        "Бизнес-класс — в объявлении названо хотя бы одно: спортзал, консьерж. "
        "Типовая новостройка 2000–2010-х — цена м² не выше 1,6 средней по району "
        "и в объявлении не названы: спортзал. "
        "Эти признаки и пороги — договорённость этого проекта, а не официальная классификация."
    )
    assert houses["prices"] == [
        {"districts": "Center", "price": "2671 USD/м²", "source": "статкомитет"},
        {"districts": "Avan, Awan", "price": "1307 USD/м²", "source": "статкомитет"},
    ]
    assert _city(document, "lisbon")["houses"]["class_note"] is None


def test_general_text_explains_marks_schedule_and_limits() -> None:
    document = build_methodology(data_dir())

    assert [mark["label"] for mark in document["marks"]] == list(MARK_LABELS_RU.values())
    assert all(mark["text"] for mark in document["marks"])
    text = json.dumps(document["sections"], ensure_ascii=False)
    assert "2 комнаты, 60 м², 2 жильца" in text
    assert "не настоящие счета" in text
    assert "30%" in text
    assert document["repo_url"] == "https://github.com/Anwert/utility-bills-compare"


def test_city_limits_come_from_the_city_file(data_root: Path) -> None:
    limits = ["не учтено вот что"]
    _edit(
        data_root / "cities" / "yerevan.json",
        lambda document: document.update(limits_ru=limits),
    )

    assert _city(build_methodology(data_root), "yerevan")["limits"] == limits


def test_the_stated_update_day_is_the_one_in_the_monthly_workflow() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")
    cron = re.search(r'cron: "\d+ \d+ (\d+) \* \*"', workflow)
    assert cron is not None
    text = json.dumps(build_methodology(data_dir())["sections"], ensure_ascii=False)
    assert f"{cron.group(1)}-го числа" in text


def test_write_methodology_puts_the_document_where_it_is_told(tmp_path: Path) -> None:
    target = write_methodology(data_dir(), tmp_path / "deep" / "methodology.json")

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == build_methodology(data_dir())


def test_a_broken_city_file_is_a_config_error(data_root: Path) -> None:
    (data_root / "cities" / "berlin.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigError, match="berlin.json"):
        build_methodology(data_root)


def test_the_yerevan_page_matches_its_rules_norms_and_hand_copied_water() -> None:
    yerevan = _city(build_methodology(data_dir()), "yerevan")

    for word in ("бассейн", "спортзал", "теннисный корт", "консьерж", "не выше 1,6"):
        assert word in yerevan["houses"]["class_note"]
    assert "не ниже" not in yerevan["houses"]["class_note"]
    for item in ("water", "sewage"):
        acting = _article(yerevan, item)["sources"][0]
        assert acting["kind"] == "официальный"
        assert "вручную" in acting["reading"]
    limits = " ".join(yerevan["limits"])
    assert "Вода и канализация переписаны вручную" in limits
    assert "2026" in limits
    assert "плиту и горячую воду" in _article(yerevan, "hot_water")["note"]
