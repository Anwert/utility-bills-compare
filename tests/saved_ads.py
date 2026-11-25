"""Saved Yerevan ads, read through the real adapters without a saved search page."""

from __future__ import annotations

from ubc.adapter import CollectContext, get_adapter
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.paths import REPO_ROOT
from ubc.schemas import BuildingCandidate, YearMonth

_MYREALTY = "https://myrealty.am/en"
_REAL_ESTATE = "https://www.real-estate.am/en/prp/apartment/buy/yerevan"
_PATHS = {
    "159730": "bnakaran/2-senyakanoc/Vardanants+St/Center/Yerevan",
    "161828": "bnakaran/2-senyakanoc/Komitas+Ave/Arabkir/Yerevan",
    "161872": "bnakaran/2-senyakanoc/Komitas+Ave/Arabkir/Yerevan",
    "162221": "bnakaran/2-senyakanoc/Arshakunyats+Ave/Shengavit/Yerevan",
    "162226": "bnakaran/2-senyakanoc/Arshakunyats+Ave/Center/Yerevan",
    "162276": "bnakaran/2-senyakanoc/Bagratuniats+Ave/Shengavit/Yerevan",
    "162327": "norakaruyc-bnakaran/2-senyakanoc/N.+Tigranyan+dead+end/Arabkir/Yerevan",
    "162339": "norakaruyc-bnakaran/2-senyakanoc/Arshakunyats+Ave/Center/Yerevan",
    "162352": "bnakaran/2-senyakanoc/M.Khorenatsi+St/Center/Yerevan",
    "162353": "norakaruyc-bnakaran/2-senyakanoc/Arshakunyats+Ave/Center/Yerevan",
    "425338": "nor-nork/lvovyan-street/2-room",
    "426106": "malatia-sebastia/qrqoryan-street-leningradyan-street/2-room",
    "427307": "kanaker-zeytun/k-ulnetsi-street/2-room",
    "427354": "davtashen/davtashen-1st-district/2-room",
    "427396": "kanaker-zeytun/davit-anhaght-street/2-room",
    "427448": "shengavit/bagratunyats-street/2-room",
    "427597": "arabkir/n-tigranyan-street-deadlock/2-room",
    "427630": "center/antarayin-street/2-room",
    "427637": "shengavit/chekhov-street/2-room",
    "427650": "center/antarayin-street/2-room",
}


class _Ads(FixtureFetcher):
    """The site shows exactly these ads, one of them optionally edited."""

    def __init__(self, adapter: str, numbers: list[str], replace: tuple[str, str]) -> None:
        site = _MYREALTY if adapter == "myrealty" else _REAL_ESTATE
        urls = {f"{site}/{_PATHS[number]}/{number}": f"{number}.html" for number in numbers}
        super().__init__(REPO_ROOT / "tests" / "fixtures" / adapter, urls)
        self._replace = replace

    def fetch_text(self, url: str) -> str:
        if url not in self.manifest:
            return "".join(f'<a href="{ad}"></a>' for ad in self.manifest)
        text = super().fetch_text(url)
        old, new = self._replace
        assert old in text
        return text.replace(old, new) if old else text


def saved_ads(*ids: str, replace: tuple[str, str] = ("", "")) -> list[BuildingCandidate]:
    """``myrealty-162353`` or ``real-estate-427307`` as the adapters return them."""

    city = load_city("yerevan")
    found = []
    for site in city.listing_sites:
        prefix = "myrealty-" if site.adapter == "myrealty" else "real-estate-"
        numbers = [name.removeprefix(prefix) for name in ids if name.startswith(prefix)]
        if not numbers:
            continue
        fetcher = _Ads(site.adapter, numbers, replace)
        ctx = CollectContext(
            city=city, period=YearMonth.parse("2026-11"), fetcher=fetcher, source=site
        )
        found.extend(get_adapter(site.adapter).collect(ctx).candidates)
    return found


def saved_ad(name: str, replace: tuple[str, str] = ("", "")) -> BuildingCandidate:
    [candidate] = saved_ads(name, replace=replace)
    return candidate
