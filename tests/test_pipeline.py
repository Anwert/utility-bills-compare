"""The local command validates data and does not fetch anything."""

from __future__ import annotations

from ubc.pipeline import main


def test_check_reports_the_six_cities_and_the_reference_apartment(capsys) -> None:
    assert main(["--check"]) == 0

    out = capsys.readouterr().out
    assert "6 cities" in out
    assert "2 rooms" in out
    assert "60 m2" in out
    assert "yerevan 2026-10" in out


def test_city_listing_shows_empty_sources(capsys) -> None:
    assert main(["--city", "yerevan"]) == 0

    assert (
        capsys.readouterr().out.strip() == "yerevan: 8 articles, 0 enabled sources, 0 listing sites"
    )
