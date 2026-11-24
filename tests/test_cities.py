"""The six city files are valid. Yerevan has tariffs; every city has FX and degree-days."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ubc.load import ConfigError, load_cities, load_city
from ubc.schemas import CHARGE_ITEMS, CityConfig

_FX = {
    "yerevan": ["fx_cba"],
    "tbilisi": ["fx_nbg"],
    "belgrade": ["fx_eurostat", "fx_nbs"],
    "warsaw": ["fx_ecb"],
    "lisbon": [],
    "berlin": [],
}

EXPECTED = {
    "yerevan": ("AMD", 40.18, 44.51, "Ереван"),
    "tbilisi": ("GEL", 41.72, 44.79, "Тбилиси"),
    "belgrade": ("RSD", 44.82, 20.46, "Белград"),
    "warsaw": ("PLN", 52.23, 21.01, "Варшава"),
    "lisbon": ("EUR", 38.72, -9.14, "Лиссабон"),
    "berlin": ("EUR", 52.52, 13.40, "Берлин"),
}


def test_six_cities_match_the_agreed_currencies_and_coordinates() -> None:
    cities = load_cities()

    assert [city.id.value for city in cities] == sorted(EXPECTED)
    for city in cities:
        currency, latitude, longitude, name_ru = EXPECTED[city.id.value]
        assert city.currency.value == currency
        assert city.latitude == latitude
        assert city.longitude == longitude
        assert city.name_ru == name_ru
        assert "$1–2" in city.paid_data_note
        assert [article.item for article in city.articles] == list(CHARGE_ITEMS)
        counts = {article.item.value: len(article.sources) for article in city.articles}
        if city.id.value == "yerevan":
            assert counts == {
                "maintenance": 1,
                "heating": 0,
                "electricity": 1,
                "gas": 1,
                "water": 2,
                "hot_water": 0,
                "sewage": 2,
                "waste": 1,
            }
        else:
            assert all(count == 0 for count in counts.values())
        assert city.listing_sites == []
        assert [source.adapter for source in city.fx_sources] == _FX[city.id.value]
        assert [source.adapter for source in city.hdd_sources] == ["open_meteo_hdd"]


def test_unknown_city_file_is_an_error() -> None:
    with pytest.raises(ConfigError, match="unknown city"):
        load_city("paris")


def test_city_rejects_a_missing_article_and_a_bad_source() -> None:
    city = load_city("yerevan").model_dump(mode="json")
    city["articles"] = [article for article in city["articles"] if article["item"] != "waste"]

    with pytest.raises(ValidationError, match="missing articles"):
        CityConfig.model_validate(city)

    broken = load_city("yerevan").model_dump(mode="json")
    broken["articles"][0]["sources"] = [
        {
            "id": "proxy",
            "adapter": "example_html",
            "kind": "statistics",
            "enabled": True,
            "reliability": "official",
            "settings": {},
        }
    ]
    with pytest.raises(ValidationError):
        CityConfig.model_validate(broken)
