"""The local command validates data and threads the collection month."""

from __future__ import annotations

import pytest

from ubc.adapter import require_listing, require_source
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.pipeline import collect_contexts, main
from ubc.schemas import ListingSite, Reliability, SourceConfig, SourceKind, YearMonth


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


def test_period_is_passed_through_to_collect_contexts(capsys, tmp_path) -> None:
    assert main(["--city", "yerevan", "--period", "2026-10"]) == 0

    assert "period 2026-10, 0 collect calls" in capsys.readouterr().out

    city = load_city("yerevan").model_copy(
        update={
            "fx_sources": [
                SourceConfig(
                    id="fx_amd",
                    adapter="fx_amd",
                    kind=SourceKind.official,
                    reliability=Reliability.official,
                    settings={"url": "https://example.test/fx"},
                )
            ],
            "listing_sites": [
                ListingSite(
                    id="myrealty",
                    adapter="myrealty",
                    url="https://example.test/listings",
                    enabled=True,
                ),
                ListingSite(id="off", adapter="off", enabled=False),
            ],
        }
    )
    contexts = collect_contexts(city, YearMonth.parse("2026-10"), FixtureFetcher(tmp_path))

    assert [type(ctx.source).__name__ for ctx in contexts] == ["SourceConfig", "ListingSite"]
    assert all(
        str(ctx.period) == "2026-10" and ctx.period.first_day.isoformat() == "2026-10-01"
        for ctx in contexts
    )
    assert require_source(contexts[0]).id == "fx_amd"
    assert require_listing(contexts[1]).adapter == "myrealty"
    with pytest.raises(TypeError):
        require_source(contexts[1])
    with pytest.raises(TypeError):
        require_listing(contexts[0])


def test_bad_period_is_rejected() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--check", "--period", "2026-13"])

    assert caught.value.code == 2
