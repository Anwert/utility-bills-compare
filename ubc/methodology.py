"""What the «Источники» tab of the site says: where data comes from and how it is priced.

The city part is generated from ``data/cities/*.json`` and the norms in
``data/reference.json``, so it cannot name a source the pipeline does not use.
The general part is written here and must describe ``calculate_cell``,
``pin_houses``, ``choose_article``, and the monthly workflow as they are.

``ubc.site_bundle`` publishes the document as ``data/methodology.json``. For a
local page write it into ``data/`` (git ignores that file):

    python -m ubc.methodology
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ubc.load import ConfigError, read_document
from ubc.paths import data_dir
from ubc.schemas import (
    HEATING_LABELS_RU,
    HOUSE_CLASS_LABELS_RU,
    ITEM_LABELS_RU,
    MARK_LABELS_RU,
    READING_LABELS_RU,
    Basis,
    CityConfig,
    CityId,
    ConsumptionNorm,
    Currency,
    HouseClass,
    HouseRules,
    Item,
    ListingSite,
    Mark,
    OpenDataKind,
    ReferenceApartment,
    ReferenceData,
    SourceConfig,
    SourceKind,
    sort_sources,
)

REPO_URL = "https://github.com/Anwert/utility-bills-compare"

_KIND_RU: dict[SourceKind, str] = {
    SourceKind.proxy_direct: "через прокси, если он настроен",
    SourceKind.official: "официальный",
    SourceKind.third_party: "сторонний сайт",
    SourceKind.statistics: "статистика",
}

_OPEN_DATA_RU: dict[OpenDataKind, str] = {
    OpenDataKind.nominatim: "Nominatim (OpenStreetMap): координаты по адресу",
    OpenDataKind.osm_levels: "OpenStreetMap: этажность дома",
    OpenDataKind.berlin_alkis: "ALKIS, кадастр Берлина: этажность дома",
    OpenDataKind.berlin_building_age: "Umweltatlas Берлина: годы застройки и вид квартала",
}

_MARK_TEXT_RU: dict[Mark, str] = {
    Mark.fixed: "сумма не зависит от расхода и погоды",
    Mark.norm_based: "тариф умножен на типичный расход, это не показания счётчика",
    Mark.statistics_estimate: "цифра взята как оценка, а не из действующего прайса поставщика",
    Mark.third_party: "цифра со стороннего сайта, не от регулятора и не от поставщика",
    Mark.llm_extracted: "число из документа достала языковая модель; сейчас этот способ выключен",
    Mark.stale: "в этом месяце нового значения нет или оно не подтвердилось, показано прошлое",
}

_NO_SOURCE_RU: dict[Item, str] = {
    Item.heating: (
        "Отдельного тарифа нет: отопление газовым котлом считается по цене газа, "
        "электрическое — по цене электричества."
    ),
}
_NO_SOURCE_DEFAULT_RU = "Отдельный источник не подключён."

_COMMODITY_RU = {"kwh": "кВт·ч", "m3": "м³", "gj": "ГДж"}
_BASIS_RU = {
    Basis.per_m2: "на м²",
    Basis.per_person: "на человека",
    Basis.per_dwelling: "на квартиру",
}


def build_methodology(data_root: Path) -> dict[str, Any]:
    """The document for the tab, from ``data_root/cities`` and ``data_root/reference.json``."""

    reference = read_document(data_root / "reference.json", ReferenceData)
    paths = sorted((data_root / "cities").glob("*.json"))
    cities = {city.id: city for city in (read_document(path, CityConfig) for path in paths)}
    return {
        "schema_version": 1,
        "repo_url": REPO_URL,
        "sections": _sections(reference.apartment),
        "marks": [
            {"label": label, "text": _MARK_TEXT_RU[mark]} for mark, label in MARK_LABELS_RU.items()
        ],
        "cities": [_city(cities[city_id], reference) for city_id in CityId if city_id in cities],
    }


def write_methodology(data_root: Path, target: Path) -> Path:
    """Write the document to ``target`` and return it."""

    document = build_methodology(data_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _city(city: CityConfig, reference: ReferenceData) -> dict[str, Any]:
    connected = any(source.enabled for article in city.articles for source in article.sources)
    return {
        "id": city.id.value,
        "name": city.name_ru,
        "notice": None
        if connected
        else (
            "Тарифы этого города ещё не подключены, поэтому суммы по нему не считаются. "
            "Ниже — то, что уже готово к моменту подключения."
        ),
        "articles": [
            {
                "item": article.item.value,
                "label": ITEM_LABELS_RU[article.item],
                "sources": _sources(article.sources),
                "note": None
                if article.sources
                else _NO_SOURCE_RU.get(article.item, _NO_SOURCE_DEFAULT_RU),
            }
            for article in city.articles
        ],
        "exchange": {
            "sources": _sources(city.fx_sources),
            "note": "Валюта города — евро, пересчёт не нужен."
            if city.currency is Currency.EUR
            else None,
        },
        "weather": _sources(city.hdd_sources),
        "houses": _houses(city),
        "norms": _norms(city.id, reference),
        "limits": list(city.limits_ru),
    }


def _sources(sources: Sequence[SourceConfig]) -> list[dict[str, Any]]:
    """In polling order, with the switched-off ones at the end."""

    ordered = sorted(sort_sources(list(sources)), key=lambda source: not source.enabled)
    return [_source(source) for source in ordered]


def _source(source: SourceConfig) -> dict[str, Any]:
    endpoint = source.settings.get("url")
    url = source.page_url or (endpoint if _is_http(endpoint) else None)
    kind = _KIND_RU[source.kind]
    return {
        "title": source.title_ru or _fallback_title(url, source.id),
        "url": url,
        "kind": kind if source.enabled else f"{kind}, выключен",
        "enabled": source.enabled,
        "reading": READING_LABELS_RU[source.reading] if source.reading else None,
    }


def _site(site: ListingSite) -> dict[str, Any]:
    url = site.page_url or site.url
    return {
        "title": site.title_ru or _fallback_title(url, site.id),
        "url": url,
        "kind": "сайт объявлений" if site.enabled else "сайт объявлений, выключен",
        "enabled": site.enabled,
        "reading": None,
    }


def _is_http(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("https://", "http://"))


def _fallback_title(url: str | None, source_id: str) -> str:
    return (urlsplit(url).netloc if url else "") or source_id


def _houses(city: CityConfig) -> dict[str, Any]:
    rules = city.house_rules
    return {
        "sites": [_site(site) for site in city.listing_sites],
        "open_data": [_OPEN_DATA_RU[source.kind] for source in city.open_data if source.enabled],
        "classes": _reasons((rule.house_class, rule.reason) for rule in rules.classes),
        "heatings": _reasons((rule.heating, rule.reason) for rule in rules.heatings),
        "price_note": _price_note(rules),
        "prices": _prices(rules),
        "unavailable": [
            {"label": HEATING_LABELS_RU[heating], "text": text}
            for heating, text in rules.unavailable_heating.items()
        ],
    }


def _reasons(pairs: Iterable[tuple[Any, str]]) -> list[dict[str, Any]]:
    """Reasons without repeats, in the order of the grid's rows or columns."""

    found: dict[Any, list[str]] = {}
    for key, reason in pairs:
        reasons = found.setdefault(key, [])
        if reason not in reasons:
            reasons.append(reason)
    labels = {**HOUSE_CLASS_LABELS_RU, **HEATING_LABELS_RU}
    return [
        {"label": label, "reasons": found[key]} for key, label in labels.items() if key in found
    ]


def _price_note(rules: HouseRules) -> str | None:
    parts: list[str] = []
    for rule in rules.classes:
        bounds = [
            f"{word} {_number(bound)}"
            for word, bound in (("от", rule.price_ratio_from), ("до", rule.price_ratio_to))
            if bound is not None
        ]
        if bounds:
            parts.append(f"{HOUSE_CLASS_LABELS_RU[rule.house_class]} — {' '.join(bounds)}")
    if not parts:
        return None
    return (
        f"Цена м² из объявления делится на среднюю цену м² в районе: {'; '.join(parts)}. "
        "Эти пороги — договорённость этого проекта, а не официальная классификация."
    )


def _prices(rules: HouseRules) -> list[dict[str, str]]:
    """Average district prices; spellings of one district share a row."""

    grouped: dict[tuple[str, str], list[str]] = {}
    for reference in rules.price_references:
        key = (f"{_number(reference.amount)} {reference.currency}/м²", reference.source)
        grouped.setdefault(key, []).append(reference.district)
    return [
        {"districts": ", ".join(districts), "price": price, "source": source}
        for (price, source), districts in grouped.items()
    ]


def _norms(city: CityId, reference: ReferenceData) -> list[dict[str, Any]]:
    groups: dict[tuple[Item, Any], dict[tuple[str, ...], list[HouseClass]]] = {}
    for norm in reference.norms:
        if norm.city is not city:
            continue
        rows = groups.setdefault((norm.item, norm.heating), {})
        rows.setdefault(_norm_row(norm, reference.apartment), []).append(norm.house_class)
    ordered = sorted(groups.items(), key=lambda group: list(Item).index(group[0][0]))
    return [
        {
            "label": ITEM_LABELS_RU[item],
            "heating": HEATING_LABELS_RU[heating] if heating else None,
            "rows": [
                {
                    "classes": _class_names(classes),
                    "value": value,
                    "flat": flat,
                    "source": source,
                    "note": note,
                }
                for (value, flat, source, note), classes in rows.items()
            ],
        }
        for (item, heating), rows in ordered
    ]


def _norm_row(norm: ConsumptionNorm, apartment: ReferenceApartment) -> tuple[str, str, str, str]:
    commodity = norm.unit.split("/", 1)[0]
    name = _COMMODITY_RU.get(commodity.lower(), commodity)
    count = {
        Basis.per_m2: apartment.area_m2,
        Basis.per_person: apartment.occupants,
        Basis.per_dwelling: 1,
    }[norm.basis]
    return (
        f"{_number(norm.annual_value)} {name} {_BASIS_RU[norm.basis]} в год",
        f"{_number(norm.annual_value * count)} {name} в год на эталонную квартиру",
        norm.source,
        norm.note,
    )


def _class_names(classes: Sequence[HouseClass]) -> str:
    if set(classes) == set(HouseClass):
        return "все классы домов"
    return "; ".join(HOUSE_CLASS_LABELS_RU[row] for row in HouseClass if row in classes)


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _sections(apartment: ReferenceApartment) -> list[dict[str, Any]]:
    flat = f"{apartment.rooms} комнаты, {apartment.area_m2} м², {apartment.occupants} жильца"
    return [
        {
            "id": "calculation",
            "title": "Как считаем",
            "lead": (
                "Плата за месяц = тариф × норма расхода, у отопления ещё и × погода этого месяца. "
                "Итог — в евро по среднему курсу месяца."
            ),
            "items": [
                f"Квартира везде одна и та же, эталонная: {flat}.",
                "Норма — сколько такая квартира обычно расходует за год. "
                "Нормы с источниками показаны выше, в части про город.",
                "Расход, который от погоды не зависит (например, свет и вода), "
                "делится на 12 месяцев поровну.",
                "Расход на отопление делится по холоду: доля месяца = его градусо-сутки / "
                "градусо-сутки отопительного года. Градусо-сутки (формула Eurostat): за каждый "
                "день со средней температурой не выше 15 °C прибавляется 18 минус эта "
                "температура, более тёплый день даёт ноль.",
                "Фиксированные платежи (обслуживание дома, мусор и подобное) расход не учитывают: "
                "это ставка за м², за человека или за квартиру.",
                "Сбор за дом берётся из объявления, если он там есть, иначе — официальная ставка "
                "города.",
                "Если цена растёт ступенями, каждая ступень действует только на свою часть "
                "расхода. Если отопление и обычный расход идут на одном ресурсе, ступень "
                "определяется по их сумме.",
                "Если тариф сменился посреди месяца, старая и новая цена берутся "
                "пропорционально дням.",
            ],
        },
        {
            "id": "houses",
            "title": "Как выбираются дома",
            "lead": "Каждая заполненная ячейка таблицы — реальный дом из объявления.",
            "items": [
                "Программа читает сайты объявлений города, недостающие координаты и этажность "
                "дополняет открытыми данными.",
                "Класс дома и тип отопления определяются по правилам города: материал, серия, "
                "год, этажность, цена м². Чего в объявлении нет, то не угадывается.",
                "На ячейку берётся один дом: тот, о котором известно больше, а при равенстве — "
                f"с площадью ближе к {apartment.area_m2} м². Он остаётся и в следующие месяцы.",
                "Если подходящего дома нет, ячейка пустая с объяснением. Оценкой она не "
                "заполняется.",
            ],
        },
        {
            "id": "updates",
            "title": "Когда обновляется и что при сбое",
            "lead": (
                "7-го числа каждого месяца GitHub Actions собирает данные за прошлый месяц и "
                "публикует их."
            ),
            "items": [
                "Источники статьи опрашиваются по порядку: официальные, затем сторонние сайты, "
                "затем статистика. Берётся первый, который ответил.",
                "Если не ответил ни один, остаётся прошлое значение с пометкой "
                f"«{MARK_LABELS_RU[Mark.stale]}».",
                "Если значение изменилось больше чем на 30%, оно не показывается, пока его не "
                "подтвердит второй источник или следующий месяц. До этого стоит прошлое "
                "значение с той же пометкой.",
                "О сбое и о скачке программа открывает задачу в репозитории на GitHub.",
                "Если для заполненной ячейки нет курса, погоды или тарифа и прошлого значения "
                "тоже нет, месяц не публикуется: вместо данных нули не подставляются.",
            ],
        },
        {
            "id": "limits",
            "title": "Что не учтено",
            "lead": (
                "Это расчёт по нормам, а не настоящие счета. Ваш счёт может заметно отличаться."
            ),
            "items": [
                "Дома за прошлые месяцы подобраны по сегодняшним объявлениям: архива объявлений "
                "нет.",
                "Аренда, ипотека, интернет, связь, парковка, страховка и налоги не входят.",
                "Льготы и субсидии не учитываются.",
                "Статья без подключённого источника в сумму не входит.",
            ],
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, nargs="?", default=data_dir() / "methodology.json")
    args = parser.parse_args(argv)
    try:
        print(write_methodology(data_dir(), args.out))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
