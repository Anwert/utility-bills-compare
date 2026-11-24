"""Yerevan listing adapters on saved pages.

Expected fields are the 24.11.2026 listing values, written as literals.
"""

from __future__ import annotations

from ubc.adapter import CollectContext, get_adapter
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import ListingSite, YearMonth

_MYREALTY = REPO_ROOT / "tests" / "fixtures" / "myrealty"
_MYREALTY_SEARCH = "https://myrealty.am/en/apartments-for-sale/yerevan"
_MYREALTY_LISTING = (
    "https://myrealty.am/en/bnakaran/2-senyakanoc/M.Khorenatsi+St/Center/Yerevan/162352"
)
_REAL_ESTATE = REPO_ROOT / "tests" / "fixtures" / "real_estate_am"
_REAL_ESTATE_SEARCH = "https://www.real-estate.am/en"
_REAL_ESTATE_LISTINGS = {
    "real-estate-427597": {
        "url": "https://www.real-estate.am/en/prp/apartment/buy/yerevan/arabkir/n-tigranyan-street-deadlock/2-room/427597",
        "address": "N. Tigranyan street deadlock, Arabkir, Yerevan",
        "latitude": 40.21282,
        "longitude": 44.5174,
        "heating_raw": "heating | gas",
        "floors": 14,
        "area_m2": 55,
        "price_per_m2": 3054.55,
    },
    "real-estate-427503": {
        "url": "https://www.real-estate.am/en/prp/apartment/buy/yerevan/arabkir/n-zaryan-street/3-room/427503",
        "address": "N. Zaryan street, Arabkir, Yerevan",
        "latitude": 40.203575,
        "longitude": 44.523691,
        "heating_raw": "heating",
        "floors": 18,
        "area_m2": 80,
        "price_per_m2": 4500,
    },
    "real-estate-416126": {
        "url": "https://www.real-estate.am/en/prp/apartment/buy/yerevan/arabkir/vratsakan-street/2-room/416126",
        "address": "Vratsakan street, Arabkir, Yerevan",
        "latitude": 40.208024,
        "longitude": 44.511803,
        "heating_raw": None,
        "floors": 14,
        "area_m2": 66,
        "price_per_m2": 3181.82,
    },
}


def _collect(adapter: str, url: str, root) -> object:
    return get_adapter(adapter).collect(
        CollectContext(
            city=load_city("yerevan"),
            period=YearMonth.parse("2026-11"),
            fetcher=FixtureFetcher(root),
            source=ListingSite(id=adapter, adapter=adapter, url=url),
        )
    )


def test_myrealty_returns_the_khorenatsi_listing() -> None:
    output = _collect("myrealty", _MYREALTY_SEARCH, _MYREALTY)

    assert output.records == []
    assert len(output.candidates) == 1
    candidate = output.candidates[0]
    assert candidate.id == "myrealty-162352"
    assert candidate.city.value == "yerevan"
    assert candidate.address == "M.Khorenatsi St, Center, Yerevan"
    assert candidate.latitude == 40.1643421
    assert candidate.longitude == 44.51712
    assert candidate.building_type == "Panel"
    assert candidate.area_m2 == 70
    assert candidate.floors == 9
    assert candidate.heating_raw == "Heating | Gas"
    assert candidate.heating is None
    assert candidate.year_built is None
    assert candidate.is_newbuild is None
    assert candidate.housing_fee is None
    assert (candidate.price_per_m2.amount, candidate.price_per_m2.currency) == (1929, "USD")
    assert candidate.url == _MYREALTY_LISTING
    assert candidate.seen_at.isoformat() == "2026-11-23"
    assert candidate.note == ""


def test_myrealty_keeps_the_central_heating_facility(tmp_path) -> None:
    for saved in _MYREALTY.iterdir():
        text = saved.read_text(encoding="utf-8")
        changed = text.replace("<label>Air-conditioner</label>", "<label>Central heating</label>")
        (tmp_path / saved.name).write_text(changed, encoding="utf-8")

    [candidate] = _collect("myrealty", _MYREALTY_SEARCH, tmp_path).candidates

    assert candidate.heating_raw == "Heating | Central heating | Gas"


def test_listing_without_a_price_is_still_a_candidate(tmp_path) -> None:
    for saved in _REAL_ESTATE.iterdir():
        text = saved.read_text(encoding="utf-8")
        changed = text.replace('rice\\":', 'riceless\\":')
        (tmp_path / saved.name).write_text(changed, encoding="utf-8")

    output = _collect("real_estate_am", _REAL_ESTATE_SEARCH, tmp_path)

    assert [item.price_per_m2 for item in output.candidates] == [None, None, None]


def test_real_estate_am_returns_the_saved_listings() -> None:
    output = _collect("real_estate_am", _REAL_ESTATE_SEARCH, _REAL_ESTATE)

    assert output.records == []
    assert len(output.candidates) == 3
    by_id = {item.id: item for item in output.candidates}
    assert set(by_id) == set(_REAL_ESTATE_LISTINGS)
    for listing_id, expected in _REAL_ESTATE_LISTINGS.items():
        candidate = by_id[listing_id]
        assert candidate.city.value == "yerevan"
        assert candidate.url == expected["url"]
        assert candidate.address == expected["address"]
        assert candidate.latitude == expected["latitude"]
        assert candidate.longitude == expected["longitude"]
        assert candidate.building_type == "MONOLITH"
        assert candidate.note == "SECONDARY_MARKET"
        assert candidate.is_newbuild is True
        assert candidate.area_m2 == expected["area_m2"]
        assert candidate.floors == expected["floors"]
        assert candidate.price_per_m2.amount == expected["price_per_m2"]
        assert candidate.price_per_m2.currency == "USD"
        assert candidate.heating_raw == expected["heating_raw"]
        assert candidate.heating is None
        assert candidate.year_built is None
        assert candidate.housing_fee is None
        assert candidate.seen_at.isoformat() == "2026-11-24"
