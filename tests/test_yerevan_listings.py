"""Yerevan listing adapters on saved pages.

Expected fields are the values shown on the saved ads (25.11.2026), written as literals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ubc.adapter import AdapterOutput, CollectContext, get_adapter
from ubc.adapters.myrealty import MyrealtyAdapter
from ubc.adapters.real_estate_am import RealEstateAmAdapter
from ubc.fetch import FetchError, FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import BuildingCandidate, ListingSite, YearMonth

_MYREALTY = REPO_ROOT / "tests" / "fixtures" / "myrealty"
_MYREALTY_SEARCH = "https://myrealty.am/hy/vacharq/bnakaranner"
_KHORENATSI = "https://myrealty.am/en/bnakaran/2-senyakanoc/M.Khorenatsi+St/Center/Yerevan/162352"
_SHIRAZ = (
    "https://myrealty.am/en/norakaruyc-bnakaran/2-senyakanoc/Shiraz+St/Ajapnyak/Yerevan/157961"
)
_REAL_ESTATE = REPO_ROOT / "tests" / "fixtures" / "real_estate_am"
_SITEMAP = "https://www.real-estate.am/sitemaps/properties/sitemap.xml"
_RE_ADS = "https://www.real-estate.am/en/prp/apartment/buy/yerevan"
_PAPAZYAN = f"{_RE_ADS}/arabkir/papazyan-street/2-room/427638"
_TIGRANYAN = f"{_RE_ADS}/arabkir/n-tigranyan-street-deadlock/2-room/427597"
_CHEKHOV = f"{_RE_ADS}/shengavit/chekhov-street/2-room/427637"
_ANTARAYIN = f"{_RE_ADS}/center/antarayin-street/2-room/427650"

_SEARCH = {"myrealty": _MYREALTY_SEARCH, "real_estate_am": _SITEMAP}
_ROOT = {"myrealty": _MYREALTY, "real_estate_am": _REAL_ESTATE}


class _Recording(FixtureFetcher):
    """Saved pages, optionally edited, and the order they were asked for."""

    def __init__(self, root: Path, *, replace: tuple[str, str] = ("", "")) -> None:
        super().__init__(root)
        self.asked: list[str] = []
        self.replace = replace

    def fetch_text(self, url: str) -> str:
        self.asked.append(url)
        old, new = self.replace
        text = super().fetch_text(url)
        return text.replace(old, new) if old else text


def _context(adapter: str, fetcher: FixtureFetcher, **settings: object) -> CollectContext:
    return CollectContext(
        city=load_city("yerevan"),
        period=YearMonth.parse("2026-11"),
        fetcher=fetcher,
        source=ListingSite(id=adapter, adapter=adapter, url=_SEARCH[adapter], settings=settings),
    )


def _collect(
    adapter: str, fetcher: FixtureFetcher | None = None, **settings: object
) -> AdapterOutput:
    fetcher = fetcher or FixtureFetcher(_ROOT[adapter])
    return get_adapter(adapter).collect(_context(adapter, fetcher, **settings))


def _by_id(output: AdapterOutput) -> dict[str, BuildingCandidate]:
    assert output.records == []
    return {candidate.id: candidate for candidate in output.candidates}


def test_myrealty_returns_the_old_fund_listing() -> None:
    candidate = _by_id(_collect("myrealty"))["myrealty-162352"]

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
    assert candidate.price_per_m2 is not None
    assert (candidate.price_per_m2.amount, candidate.price_per_m2.currency) == (1929, "USD")
    assert candidate.url == _KHORENATSI
    assert candidate.seen_at.isoformat() == "2026-11-23"
    assert candidate.note == ""


def test_myrealty_returns_the_new_building_listing() -> None:
    candidate = _by_id(_collect("myrealty"))["myrealty-157961"]

    assert candidate.address == "Shiraz St, Ajapnyak, Yerevan"
    assert candidate.building_type == "Monolit"
    assert candidate.is_newbuild is True
    assert candidate.area_m2 == 53
    assert candidate.floors == 13
    assert candidate.heating_raw == "Heating"
    assert candidate.price_per_m2 is not None
    assert (candidate.price_per_m2.amount, candidate.price_per_m2.currency) == (2264, "USD")
    assert candidate.url == _SHIRAZ
    assert candidate.seen_at.isoformat() == "2026-11-23"


def test_myrealty_takes_only_two_room_sale_ads_and_survives_missing_pages() -> None:
    fetcher = _Recording(_MYREALTY)

    output = _collect("myrealty", fetcher)

    # 162327 now redirects to the home page, 160957 and 162007 have no saved page,
    # and page 3 of the search is not saved either.
    assert set(_by_id(output)) == {"myrealty-162352", "myrealty-157961"}
    asked = "\n".join(fetcher.asked)
    assert "varcov" not in asked
    assert "3-senyakanoc" not in asked and "4-senyakanoc" not in asked
    assert "order=" not in asked
    assert "/160957" in asked and "/162007" in asked
    assert "https://myrealty.am/hy/bnakaranneri-vacharq/7784?page=2" in fetcher.asked


def test_myrealty_shares_the_cap_between_old_and_new_buildings() -> None:
    fetcher = _Recording(_MYREALTY)

    output = _collect("myrealty", fetcher, max_ads=2)

    assert set(_by_id(output)) == {"myrealty-162352", "myrealty-157961"}
    assert len(fetcher.asked) == 3


def test_myrealty_stops_paging_at_the_configured_page() -> None:
    fetcher = _Recording(_MYREALTY)

    _collect("myrealty", fetcher, max_pages=1)

    assert not [url for url in fetcher.asked if "page=" in url]


def test_myrealty_address_is_decoded(tmp_path: Path) -> None:
    href = (
        "norakaruyc-bnakaran/2-senyakanoc/M%E2%80%A4+Melqonyan+++str/Malatia-Sebastia/Yerevan/1573"
    )
    (tmp_path / "search.html").write_text(
        f'<a href="https://myrealty.am/hy/{href}" class="image">', encoding="utf-8"
    )
    ad = (_MYREALTY / "157961.html").read_text(encoding="utf-8").replace("157961", "1573")
    (tmp_path / "ad.html").write_text(ad, encoding="utf-8")
    manifest = {_MYREALTY_SEARCH: "search.html", f"https://myrealty.am/en/{href}": "ad.html"}

    [candidate] = _collect("myrealty", FixtureFetcher(tmp_path, manifest)).candidates

    assert candidate.address == "M. Melqonyan str, Malatia-Sebastia, Yerevan"


def test_myrealty_keeps_the_central_heating_facility() -> None:
    central = ("<label>Air-conditioner</label>", "<label>Central heating</label>")

    found = _by_id(_collect("myrealty", _Recording(_MYREALTY, replace=central)))

    assert found["myrealty-162352"].heating_raw == "Heating | Central heating | Gas"


def test_real_estate_am_returns_the_saved_listings() -> None:
    found = _by_id(_collect("real_estate_am"))

    # 427650 is in the sitemap but its page is not saved.
    assert set(found) == {"real-estate-427597", "real-estate-427637", "real-estate-427638"}
    stone = found["real-estate-427638"]
    assert stone.url == _PAPAZYAN
    assert stone.address == "Papazyan street, Arabkir, Yerevan"
    assert (stone.latitude, stone.longitude) == (40.205198, 44.50264)
    assert stone.building_type == "STONE"
    assert stone.is_newbuild is False
    assert stone.heating_raw is None
    assert (stone.floors, stone.area_m2) == (7, 74)
    assert stone.price_per_m2 is not None
    assert stone.price_per_m2.amount == 2162.16
    assert stone.seen_at.isoformat() == "2026-11-22"
    monolith = found["real-estate-427597"]
    assert monolith.url == _TIGRANYAN
    assert monolith.address == "N. Tigranyan street deadlock, Arabkir, Yerevan"
    assert (monolith.latitude, monolith.longitude) == (40.21282, 44.5174)
    assert monolith.building_type == "MONOLITH"
    assert monolith.is_newbuild is True
    assert monolith.heating_raw == "heating | gas"
    assert (monolith.floors, monolith.area_m2) == (14, 55)
    # 168,000 USD, the price the page shows, not the first asking price 170,000.
    assert monolith.price_per_m2 is not None
    assert (monolith.price_per_m2.amount, monolith.price_per_m2.currency) == (3054.55, "USD")
    assert monolith.seen_at.isoformat() == "2026-11-24"
    panel = found["real-estate-427637"]
    assert panel.url == _CHEKHOV
    assert panel.building_type == "PANEL"
    assert panel.heating_raw == "heating | gas"
    # The ad has no area, so there is no price per m2 either.
    assert panel.area_m2 is None and panel.price_per_m2 is None
    for candidate in found.values():
        assert candidate.city.value == "yerevan"
        assert candidate.heating is None
        assert candidate.year_built is None
        assert candidate.housing_fee is None
        assert candidate.note == "SECONDARY_MARKET"


def test_real_estate_am_asks_the_newest_ad_of_every_district_first() -> None:
    fetcher = _Recording(_REAL_ESTATE)

    _collect("real_estate_am", fetcher)

    assert fetcher.asked == [_SITEMAP, _ANTARAYIN, _PAPAZYAN, _CHEKHOV, _TIGRANYAN]


def test_real_estate_am_stops_at_the_cap() -> None:
    fetcher = _Recording(_REAL_ESTATE)

    _collect("real_estate_am", fetcher, max_ads=2)

    assert fetcher.asked == [_SITEMAP, _ANTARAYIN, _PAPAZYAN]


@pytest.mark.parametrize(
    "replace",
    [
        (r"\"propertyStatus\":\"ACTIVE\"", r"\"propertyStatus\":\"SOLD\""),
        (r"\"removed\":false,\"utilities\"", r"\"removed\":true,\"utilities\""),
        (r"\"propertyActionType\":\"SALE\"", r"\"propertyActionType\":\"RENT\""),
    ],
    ids=["sold", "removed", "rent"],
)
def test_real_estate_am_skips_ads_that_are_not_a_live_sale(replace: tuple[str, str]) -> None:
    with pytest.raises(ValueError, match="no listing"):
        _collect("real_estate_am", _Recording(_REAL_ESTATE, replace=replace))


def test_real_estate_am_reads_escaped_letters_in_an_address() -> None:
    fetcher = _Recording(_REAL_ESTATE, replace=("Papazyan street", r"V․ Papazyan \\u0026  Sons"))

    found = _by_id(_collect("real_estate_am", fetcher))

    assert found["real-estate-427638"].address == "V. Papazyan & Sons, Arabkir, Yerevan"


def test_listing_without_a_price_is_still_a_candidate() -> None:
    fetcher = _Recording(_REAL_ESTATE, replace=(r"rice\":", r"riceless\":"))

    output = _collect("real_estate_am", fetcher)

    assert [item.price_per_m2 for item in output.candidates] == [None, None, None]


@pytest.mark.parametrize(
    ("adapter", "replace", "lost"),
    [
        ("myrealty", ("<span>4/13</span>", "<span>4/0</span>"), "myrealty-157961"),
        ("real_estate_am", (r"\"totalFloor\":14", r"\"totalFloor\":0"), "real-estate-427597"),
    ],
)
def test_one_ad_with_impossible_fields_does_not_lose_the_others(
    adapter: str, replace: tuple[str, str], lost: str
) -> None:
    whole = set(_by_id(_collect(adapter)))

    found = set(_by_id(_collect(adapter, _Recording(_ROOT[adapter], replace=replace))))

    assert found == whole - {lost}


@pytest.mark.parametrize("adapter", ["myrealty", "real_estate_am"])
def test_a_source_with_no_readable_ad_fails(adapter: str, tmp_path: Path) -> None:
    search = _SEARCH[adapter]
    manifest = json.loads((_ROOT[adapter] / "manifest.json").read_text(encoding="utf-8"))
    (tmp_path / "empty.html").write_text("<html></html>", encoding="utf-8")

    with pytest.raises(ValueError, match="no listing"):
        _collect(adapter, FixtureFetcher(_ROOT[adapter], {search: manifest[search]}))
    with pytest.raises(ValueError, match="no listing"):
        _collect(adapter, FixtureFetcher(tmp_path, {search: "empty.html"}))
    with pytest.raises(FetchError):
        _collect(adapter, FixtureFetcher(tmp_path, {}))


@pytest.mark.parametrize("adapter", [MyrealtyAdapter, RealEstateAmAdapter])
def test_every_request_after_the_first_waits(
    adapter: type[MyrealtyAdapter] | type[RealEstateAmAdapter],
) -> None:
    waits: list[float] = []
    fetcher = _Recording(_ROOT[adapter.name])

    adapter(sleep=waits.append).collect(_context(adapter.name, fetcher, pause_seconds=0.5))

    assert waits == [0.5] * (len(fetcher.asked) - 1)
