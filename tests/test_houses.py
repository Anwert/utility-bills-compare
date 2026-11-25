"""Listing candidates become pinned houses, one per cell, kept from month to month.

The city rules are the real ones from ``data/cities``.
"""

from __future__ import annotations

import pytest

from saved_ads import saved_ad, saved_ads
from ubc.adapter import CollectContext, get_adapter
from ubc.fetch import FixtureFetcher
from ubc.houses import PinResult, pin_houses
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import (
    BuildingCandidate,
    HeatingType,
    HouseClass,
    PinnedHouse,
    YearMonth,
)

_SOVIET_GAS = (HouseClass.soviet_panel, HeatingType.gas_boiler)
_TYPICAL_GAS = (HouseClass.typical_newbuild, HeatingType.gas_boiler)
_BUSINESS_GAS = (HouseClass.business, HeatingType.gas_boiler)
_SOVIET_ELECTRIC = (HouseClass.soviet_panel, HeatingType.electric)
_TYPICAL_DISTRICT = (HouseClass.typical_newbuild, HeatingType.district)
_SOVIET_DISTRICT = (HouseClass.soviet_panel, HeatingType.district)
_NOT_FOUND = "дом такого класса с таким отоплением автоматически не найден"


def candidate(city: str, **facts: object) -> BuildingCandidate:
    data: dict = {
        "id": f"{city}-1",
        "city": city,
        "address": "пример",
        "url": f"https://example.test/{city}/1",
        "seen_at": "2026-11-24",
    }
    data.update(facts)
    return BuildingCandidate.model_validate(data)


def panel(**facts: object) -> BuildingCandidate:
    """myrealty-162352 as collected on 25.11.2026."""

    data: dict = {
        "id": "myrealty-162352",
        "address": "M.Khorenatsi St, Center, Yerevan",
        "latitude": 40.1643421,
        "longitude": 44.51712,
        "heating_raw": "Heating | Gas",
        "building_type": "Panel",
        "floors": 9,
        "area_m2": 70,
        "url": "https://myrealty.am/en/bnakaran/2-senyakanoc/M.Khorenatsi+St/Center/Yerevan/162352",
        "seen_at": "2026-11-23",
    }
    data.update(facts)
    return candidate("yerevan", **data)


def monolith(**facts: object) -> BuildingCandidate:
    """real-estate-427597 as collected on 25.11.2026."""

    data: dict = {
        "id": "real-estate-427597",
        "address": "N. Tigranyan street deadlock, Arabkir, Yerevan",
        "latitude": 40.21282,
        "longitude": 44.5174,
        "heating_raw": "heating | gas",
        "building_type": "MONOLITH",
        "is_newbuild": True,
        "floors": 14,
        "area_m2": 55,
        "price_per_m2": {"amount": 3054.55, "currency": "USD"},
        "url": "https://www.real-estate.am/en/prp/apartment/buy/yerevan/arabkir/n-tigranyan-street-deadlock/2-room/427597",
        "note": "SECONDARY_MARKET",
    }
    data.update(facts)
    return candidate("yerevan", **data)


def pin(
    city: str,
    *candidates: BuildingCandidate,
    previous: tuple[PinnedHouse, ...] = (),
    period: str = "2026-11",
) -> PinResult:
    result = pin_houses(
        city=load_city(city),
        candidates=candidates,
        previous=previous,
        period=YearMonth.parse(period),
    )
    cells = {(row, column) for row in HouseClass for column in HeatingType}
    assert set(result.houses) | set(result.empty_reasons) == cells
    assert not set(result.houses) & set(result.empty_reasons)
    return result


def cells(result: PinResult) -> list[tuple[HouseClass, HeatingType]]:
    return list(result.houses)


def test_yerevan_panel_with_gas_becomes_the_soviet_gas_house() -> None:
    result = pin("yerevan", panel())

    house = result.houses[_SOVIET_GAS]
    assert house.id == "myrealty-162352"
    assert house.house_class is HouseClass.soviet_panel
    assert house.heating is HeatingType.gas_boiler
    assert house.address == "M.Khorenatsi St, Center, Yerevan"
    assert (house.latitude, house.longitude) == (40.1643421, 44.51712)
    assert house.listing_url == (
        "https://myrealty.am/en/bnakaran/2-senyakanoc/M.Khorenatsi+St/Center/Yerevan/162352"
    )
    assert house.first_seen.isoformat() == "2026-11-23"
    assert house.last_seen.isoformat() == "2026-11-23"
    assert house.housing_fees == []
    assert house.note == ""
    assert cells(result) == [_SOVIET_GAS]


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # Arabkir averages 1911 USD/m2: 3055 is just under 1.6 of it.
        ({}, [_TYPICAL_GAS]),
        # A high price alone is a renovation or a view, not a business-class house.
        ({"price_per_m2": {"amount": 3100, "currency": "USD"}}, []),
        ({"price_per_m2": {"amount": 4500, "currency": "USD"}}, []),
        # A pool, a gym, a tennis court or a concierge of the complex is.
        ({"amenities": ["swimming_pool"]}, [_BUSINESS_GAS]),
        ({"amenities": ["gym"], "price_per_m2": None}, [_BUSINESS_GAS]),
        ({"amenities": ["concierge"], "address": None}, [_BUSINESS_GAS]),
        ({"amenities": ["tennis"], "is_newbuild": False}, []),
        ({"amenities": ["gym"], "building_type": "Panel", "is_newbuild": False}, [_SOVIET_GAS]),
        # The price of a bare shell proves nothing either way, the building still counts.
        ({"unfinished": True}, []),
        ({"unfinished": True, "amenities": ["gym"]}, [_BUSINESS_GAS]),
        ({"price_per_m2": None}, []),
        ({"price_per_m2": {"amount": 1100000, "currency": "AMD"}}, []),
        ({"address": "Anastas Mikoyan street, Davtashen, Yerevan"}, []),
        ({"address": None}, []),
        ({"is_newbuild": None}, []),
        ({"is_newbuild": False}, []),
        ({"building_type": "Panel"}, []),
    ],
)
def test_yerevan_monolith_class_needs_amenities_or_an_ordinary_price(
    facts: dict, expected: list
) -> None:
    assert cells(pin("yerevan", monolith(**facts))) == expected


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"building_type": "Stone", "floors": 5}, [_SOVIET_GAS]),
        ({"building_type": "Stone", "floors": 5, "series": "stalinka"}, []),
        ({"series": "stalinka"}, [_SOVIET_GAS]),
        ({"building_type": "Stone", "floors": 2}, []),
        ({"building_type": "Stone", "floors": 9}, []),
        ({"building_type": "Stone", "floors": None}, []),
        ({"building_type": "Panel", "is_newbuild": True}, []),
        ({"building_type": None}, []),
        ({"heating_raw": "Heating"}, []),
        ({"heating_raw": "Gas"}, []),
        ({"heating_raw": None}, []),
        ({"heating_raw": "Heating | Central heating"}, []),
        ({"heating_raw": "Heating | Gas | Central heating"}, []),
        ({"heating_raw": None, "heating": "gas_boiler"}, [_SOVIET_GAS]),
        # The text says how the flat is heated, the gas is for the stove.
        ({"heating_raw": "Heating | Gas | text_air_heating | text_floor_heating"}, []),
        ({"heating_raw": "Heating | Gas | text_electric_heating"}, [_SOVIET_ELECTRIC]),
        ({"heating_raw": "text_electric_heating"}, [_SOVIET_ELECTRIC]),
        ({"heating_raw": "Heating | text_gas_boiler"}, [_SOVIET_GAS]),
        ({"heating_raw": "text_gas_boiler"}, [_SOVIET_GAS]),
        ({"heating_raw": "text_gas_boiler | text_electric_heating"}, []),
        ({"heating_raw": "text_gas_boiler | Central heating"}, []),
        ({"heating_raw": "text_gas_boiler | text_air_heating"}, []),
        ({"heating_raw": "Gas | text_individual_heating"}, [_SOVIET_GAS]),
        ({"heating_raw": "text_individual_heating | text_gas_supply"}, [_SOVIET_GAS]),
        ({"heating_raw": "Heating | text_individual_heating"}, []),
        ({"heating_raw": "Heating | text_gas_supply"}, []),
    ],
)
def test_yerevan_old_stock_and_heating_words(facts: dict, expected: list) -> None:
    assert cells(pin("yerevan", panel(**facts))) == expected


@pytest.mark.parametrize(
    ("city", "facts", "expected"),
    [
        # otodom: heating code, build year, czynsz per flat.
        (
            "warsaw",
            {"heating_raw": "urban", "year_built": 2003, "area_m2": 50.79},
            ("typical_newbuild", "district"),
        ),
        (
            "warsaw",
            {
                "heating_raw": "urban",
                "year_built": 2003,
                "area_m2": 50.79,
                "housing_fee": {
                    "amount": 1300,
                    "currency": "PLN",
                    "period": "month",
                    "per": "per_dwelling",
                },
            },
            ("business", "district"),
        ),
        ("warsaw", {"heating_raw": "gas", "year_built": 1974}, ("soviet_panel", "gas_boiler")),
        ("warsaw", {"heating_raw": "electrical", "year_built": 2022}, ("business", "electric")),
        ("warsaw", {"heating_raw": "urban", "year_built": 2027}, None),
        ("warsaw", {"heating_raw": "urban", "year_built": 1995}, None),
        ("warsaw", {"heating_raw": "urban", "year_built": 1932}, None),
        ("warsaw", {"heating_raw": "boiler_room", "year_built": 2003}, None),
        # halooglasi: grejanje_s and tip_objekta_s, almost never a year.
        (
            "belgrade",
            {"heating_raw": "CG", "building_type": "Stara gradnja", "floors": 10},
            ("soviet_panel", "district"),
        ),
        ("belgrade", {"heating_raw": "CG", "building_type": "Stara gradnja", "floors": 4}, None),
        ("belgrade", {"heating_raw": "CG", "building_type": "Stara gradnja"}, None),
        ("belgrade", {"heating_raw": "EG", "year_built": 1975}, None),
        # 4zida: "Grejanje:" and "Godina izgradnje".
        (
            "belgrade",
            {"heating_raw": "Centralno", "year_built": 1975},
            ("soviet_panel", "district"),
        ),
        (
            "belgrade",
            {"heating_raw": "Struja", "year_built": 2012},
            ("typical_newbuild", "electric"),
        ),
        ("belgrade", {"heating_raw": "Gas", "year_built": 2021}, ("business", "gas_boiler")),
        # kleinanzeigen: Heizungsart, Energieträger, Baujahr.
        (
            "berlin",
            {"heating_raw": "Fernwärme", "building_type": "Plattenbau"},
            ("soviet_panel", "district"),
        ),
        (
            "berlin",
            {"heating_raw": "Etagenheizung | Gas", "year_built": 2005},
            ("typical_newbuild", "gas_boiler"),
        ),
        ("berlin", {"heating_raw": "Etagenheizung", "year_built": 2005}, None),
        ("berlin", {"heating_raw": "Elektrisch", "year_built": 2021}, ("business", "electric")),
        (
            "berlin",
            {
                "heating_raw": "Zentralheizung",
                "year_from": 1951,
                "year_to": 1960,
                "block_type": "Großsiedlungen und Punkthochhäuser (1960er-1980er), 4–11-geschossig",
            },
            ("soviet_panel", "district"),
        ),
        (
            "berlin",
            {"heating_raw": "Fernwärme", "year_from": 1981, "year_to": 1990, "floors": 11},
            ("soviet_panel", "district"),
        ),
        ("berlin", {"heating_raw": "Fernwärme", "year_to": 1900, "floors": 5}, None),
        # imovirtual: Heating and Build_year.
        ("lisbon", {"heating_raw": "electric", "year_built": 1978}, ("soviet_panel", "electric")),
        ("lisbon", {"heating_raw": "gas", "year_built": 2008}, ("typical_newbuild", "gas_boiler")),
        ("lisbon", {"heating_raw": "central", "year_built": 2008}, None),
        # myhome.ge: heating_type_id, project_type_id, build-year ranges.
        ("tbilisi", {"heating_raw": "2", "series": "8"}, ("soviet_panel", "gas_boiler")),
        ("tbilisi", {"heating_raw": "3", "series": "ხრუშოვის"}, ("soviet_panel", "electric")),
        (
            "tbilisi",
            {"heating_raw": "2", "year_from": 1955, "year_to": 2000, "is_newbuild": False},
            ("soviet_panel", "gas_boiler"),
        ),
        ("tbilisi", {"heating_raw": "2", "year_from": 2000}, None),
        ("tbilisi", {"heating_raw": "2", "year_built": 2014}, ("typical_newbuild", "gas_boiler")),
        ("tbilisi", {"heating_raw": "1", "series": "8"}, None),
    ],
)
def test_each_sites_words_reach_the_expected_cell(
    city: str, facts: dict, expected: tuple[str, str] | None
) -> None:
    wanted = [] if expected is None else [(HouseClass(expected[0]), HeatingType(expected[1]))]

    assert cells(pin(city, candidate(city, **facts))) == wanted


def test_district_column_of_yerevan_and_tbilisi_is_never_pinned() -> None:
    boiler_house = panel(heating_raw=None, heating="district")
    was_pinned = PinnedHouse(
        id="old",
        city="yerevan",
        house_class="typical_newbuild",
        heating="district",
        address="пример",
        first_seen="2026-09-01",
        last_seen="2026-10-01",
    )

    yerevan = pin("yerevan", boiler_house, previous=(was_pinned,))
    tbilisi = pin("tbilisi", candidate("tbilisi", heating="district", series="8"))

    assert cells(yerevan) == [] and cells(tbilisi) == []
    assert "нет городского тарифа центрального отопления" in yerevan.empty_reasons[_SOVIET_DISTRICT]
    assert "нет городского тарифа центрального отопления" in tbilisi.empty_reasons[_SOVIET_DISTRICT]
    assert yerevan.empty_reasons[_TYPICAL_GAS] == _NOT_FOUND


def test_empty_cell_says_when_no_listing_was_collected_at_all() -> None:
    assert pin("warsaw").empty_reasons[_TYPICAL_DISTRICT] == (
        "сайты объявлений в этом месяце не дали ни одного дома"
    )
    other_class = candidate("warsaw", heating_raw="gas", year_built=1974)
    assert pin("warsaw", other_class).empty_reasons[_TYPICAL_DISTRICT] == _NOT_FOUND


def test_the_house_with_the_most_complete_facts_is_pinned() -> None:
    bare = panel(id="bare", address=None, latitude=None, longitude=None)
    full = panel(id="full")
    far_from_reference = panel(id="a-larger", area_m2=95)

    assert pin("yerevan", bare, far_from_reference, full).houses[_SOVIET_GAS].id == "full"


def test_an_explicitly_stated_gas_boiler_wins_over_the_tags_alone() -> None:
    complete = panel(id="a-complete", area_m2=60)
    stated = panel(id="stated", area_m2=80, latitude=None, longitude=None)
    stated = stated.model_copy(update={"heating_raw": "text_gas_boiler"})

    assert pin("yerevan", complete, stated).houses[_SOVIET_GAS].id == "stated"


def test_the_same_flat_on_two_sites_is_one_candidate() -> None:
    real_estate, myrealty = sorted(
        saved_ads("real-estate-427597", "myrealty-162327"), key=lambda ad: ad.id, reverse=True
    )
    poorer = myrealty.model_copy(update={"building_type": None})

    assert pin("yerevan", poorer, real_estate).houses[_TYPICAL_GAS].id == "real-estate-427597"
    # A pool or bare walls that only one of the two ads mentions belong to the flat.
    pool = poorer.model_copy(update={"amenities": ["swimming_pool"]})
    shell = poorer.model_copy(update={"unfinished": True})
    assert pin("yerevan", real_estate, pool).houses[_BUSINESS_GAS].id == "real-estate-427597"
    assert cells(pin("yerevan", real_estate, shell)) == []


@pytest.mark.parametrize(
    ("facts", "same"),
    [
        ({}, True),
        # 136 m north of the first point, still the pin of the same house.
        ({"latitude": 40.16556}, True),
        ({"latitude": 40.1693}, False),
        ({"area_m2": 73}, False),
        ({"floor": 6}, False),
        ({"floor": None}, False),
        ({"floors": 10}, False),
        ({"price_per_m2": {"amount": 2000, "currency": "USD"}}, False),
        ({"price_per_m2": {"amount": 1929, "currency": "EUR"}}, False),
        ({"price_per_m2": None}, False),
        ({"latitude": None, "longitude": None}, False),
    ],
)
def test_what_makes_two_ads_the_same_flat(facts: dict, same: bool) -> None:
    flat = panel(floor=5, price_per_m2={"amount": 1929, "currency": "USD"})
    other: dict = {"floor": 5, "price_per_m2": {"amount": 1929, "currency": "USD"}, **facts}
    # The other ad names central heating. One flat cannot have both, two flats can.
    contradicting = panel(id="real-estate-1", heating_raw="centralized_heating", **other)

    result = pin("yerevan", flat, contradicting)

    assert cells(result) == ([] if same else [_SOVIET_GAS])


@pytest.mark.parametrize(
    "ads",
    [
        # Stalinka on one site, an ordinary stone house on the other.
        ("real-estate-427448", "myrealty-162276"),
        ("myrealty-161828", "myrealty-161872"),
        # One ad says Shengavit, the other says Center.
        ("myrealty-162221", "myrealty-162226"),
    ],
)
def test_ads_of_one_flat_that_contradict_each_other_are_not_classified(
    ads: tuple[str, str],
) -> None:
    assert cells(pin("yerevan", *saved_ads(*ads))) == []


def test_one_of_those_ads_alone_would_have_been_pinned() -> None:
    assert cells(pin("yerevan", saved_ad("myrealty-162276"))) == [_SOVIET_GAS]
    assert cells(pin("yerevan", saved_ad("myrealty-162226"))) == [_SOVIET_GAS]


@pytest.mark.parametrize(
    "other",
    [
        {"building_type": "STONE"},
        {"is_newbuild": True},
        {"heating": "electric", "heating_raw": None},
    ],
)
def test_one_flat_with_different_construction_or_heating_is_not_classified(other: dict) -> None:
    facts: dict = {"floor": 5, "price_per_m2": {"amount": 1929, "currency": "USD"}}
    flat = panel(is_newbuild=False, heating="gas_boiler", **facts)
    again = panel(id="real-estate-1", **{"is_newbuild": False, **facts, **other})

    assert cells(pin("yerevan", flat, again)) == []


def test_a_duplicate_does_not_move_the_pinned_house() -> None:
    pages = saved_ads("real-estate-427597", "myrealty-162327")
    november = pin("yerevan", pages[0]).houses[_TYPICAL_GAS]

    december = pin("yerevan", *pages, previous=(november,), period="2026-12")

    assert cells(december) == [_TYPICAL_GAS]
    assert december.houses[_TYPICAL_GAS].id == november.id


@pytest.mark.parametrize(
    ("ad", "expected"),
    [
        ("real-estate-427597", [_TYPICAL_GAS]),
        ("real-estate-427307", [_BUSINESS_GAS]),
        ("real-estate-427630", [_BUSINESS_GAS]),
        ("real-estate-427650", [_BUSINESS_GAS]),
        ("myrealty-162352", [_SOVIET_GAS]),
        # Furnished flats in ordinary buildings, dear but without amenities.
        ("real-estate-425338", []),
        ("real-estate-427354", []),
        # A bare shell in an ordinary building.
        ("real-estate-426106", []),
        # Air conditioner, fan coils, a house boiler room.
        ("myrealty-159730", []),
        ("real-estate-427396", []),
        ("myrealty-162353", []),
        # Stalin-era stone.
        ("myrealty-161828", []),
        ("real-estate-427448", []),
    ],
)
def test_saved_ad_reaches_the_cell_a_person_would_choose(ad: str, expected: list) -> None:
    assert cells(pin("yerevan", saved_ad(ad))) == expected


def test_candidates_and_pins_of_another_city_are_ignored() -> None:
    result = pin("warsaw", panel(), previous=(pin("yerevan", panel()).houses[_SOVIET_GAS],))

    assert cells(result) == []


def pinned_in_november(**facts: object) -> PinnedHouse:
    return pin("yerevan", panel(**facts)).houses[_SOVIET_GAS]


def test_the_pinned_house_stays_when_a_better_listing_appears() -> None:
    november = pinned_in_november(latitude=None, longitude=None)
    better = panel(id="myrealty-999", area_m2=60, seen_at="2026-12-20")
    still_listed = panel(seen_at="2026-12-15")

    december = pin("yerevan", better, still_listed, previous=(november,), period="2026-12")

    house = december.houses[_SOVIET_GAS]
    assert house.id == "myrealty-162352"
    assert (house.latitude, house.longitude) == (40.1643421, 44.51712)
    assert house.first_seen.isoformat() == "2026-11-23"
    assert house.last_seen.isoformat() == "2026-12-15"


def test_a_listed_ad_is_seen_this_month_even_if_its_edit_date_is_old() -> None:
    house = pin("yerevan", panel(), previous=(pinned_in_november(),), period="2027-01").houses[
        _SOVIET_GAS
    ]

    assert house.last_seen.isoformat() == "2027-01-01"


def test_recalculating_an_earlier_month_does_not_move_last_seen_back() -> None:
    december = pin("yerevan", panel(), previous=(pinned_in_november(),), period="2026-12").houses[
        _SOVIET_GAS
    ]
    again = pin("yerevan", panel(), previous=(december,), period="2026-10").houses[_SOVIET_GAS]

    assert again.last_seen.isoformat() == "2026-12-01"


def test_a_vanished_listing_keeps_the_house_with_its_last_facts_and_link() -> None:
    november = pinned_in_november()
    other = panel(id="myrealty-999", seen_at="2026-12-20")

    with_others = pin("yerevan", other, previous=(november,), period="2026-12")
    with_nothing = pin("yerevan", previous=(november,), period="2026-12")

    assert with_others.houses[_SOVIET_GAS] == november
    assert with_nothing.houses[_SOVIET_GAS] == november


def test_a_listing_that_lost_the_deciding_facts_keeps_its_house() -> None:
    silent = panel(heating_raw=None, building_type=None, seen_at="2026-12-15")

    house = pin("yerevan", silent, previous=(pinned_in_november(),), period="2026-12").houses[
        _SOVIET_GAS
    ]

    assert house.id == "myrealty-162352"
    assert house.last_seen.isoformat() == "2026-12-15"


def test_a_house_whose_listing_now_belongs_to_another_cell_is_replaced() -> None:
    november = pin("yerevan", monolith()).houses[_TYPICAL_GAS]
    repriced = monolith(amenities=["swimming_pool"])
    replacement = monolith(id="real-estate-2")

    december = pin("yerevan", repriced, replacement, previous=(november,), period="2026-12")

    assert december.houses[_TYPICAL_GAS].id == "real-estate-2"
    assert december.houses[_BUSINESS_GAS].id == "real-estate-427597"


def test_a_house_removed_from_the_state_is_chosen_again() -> None:
    replacement = panel(id="myrealty-999")

    assert pin("yerevan", replacement).houses[_SOVIET_GAS].id == "myrealty-999"


def test_listing_fee_follows_the_ad_and_its_note_travels_with_it() -> None:
    fee = {"amount": 900, "currency": "PLN", "period": "month", "per": "per_dwelling"}
    ad = {"heating_raw": "urban", "year_built": 2003, "area_m2": 60}
    with_fee = candidate("warsaw", housing_fee=fee, note="czynsz с авансами", **ad)

    november = pin("warsaw", with_fee).houses[_TYPICAL_DISTRICT]
    gone = pin("warsaw", previous=(november,), period="2026-12").houses[_TYPICAL_DISTRICT]
    without_fee = pin("warsaw", candidate("warsaw", note="SECONDARY", **ad)).houses[
        _TYPICAL_DISTRICT
    ]

    assert [item.amount for item in november.housing_fees] == [900]
    assert november.note == "czynsz с авансами"
    assert gone.housing_fees == november.housing_fees
    assert gone.listing_url == "https://example.test/warsaw/1"
    assert without_fee.housing_fees == []
    assert without_fee.note == ""


def test_saved_yerevan_pages_fill_the_soviet_and_typical_gas_cells() -> None:
    city = load_city("yerevan")
    found: list[BuildingCandidate] = []
    for site in city.listing_sites:
        fetcher = FixtureFetcher(REPO_ROOT / "tests" / "fixtures" / site.adapter)
        ctx = CollectContext(
            city=city, period=YearMonth.parse("2026-11"), fetcher=fetcher, source=site
        )
        found.extend(get_adapter(site.adapter).collect(ctx).candidates)

    result = pin("yerevan", *found)

    assert {key: house.id for key, house in result.houses.items()} == {
        _SOVIET_GAS: "myrealty-162352",
        _TYPICAL_GAS: "real-estate-427597",
    }
