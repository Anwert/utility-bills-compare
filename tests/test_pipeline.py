"""The local command validates data and threads the collection month."""

from __future__ import annotations

from datetime import date

import pytest

from ubc.adapter import require_listing, require_source
from ubc.fetch import FixtureFetcher
from ubc.load import load_city
from ubc.monthly import MonthOptions, MonthReport, PublicationError
from ubc.pipeline import collect_contexts, main
from ubc.schemas import Item, ListingSite, Reliability, SourceConfig, SourceKind, YearMonth


def test_check_reports_the_six_cities_and_the_reference_apartment(capsys) -> None:
    assert main(["--check"]) == 0

    out = capsys.readouterr().out
    assert "6 cities" in out
    assert "2 rooms" in out
    assert "60 m2" in out
    assert "yerevan 2026-10" in out


def test_city_listing_counts_enabled_sources(capsys) -> None:
    assert main(["--city", "yerevan"]) == 0

    assert (
        capsys.readouterr().out.strip() == "yerevan: 8 articles, 8 enabled sources, 2 listing sites"
    )


def test_period_is_passed_through_to_collect_contexts(capsys, tmp_path) -> None:
    assert main(["--city", "yerevan", "--period", "2026-10"]) == 0

    assert "period 2026-10, 10 collect calls" in capsys.readouterr().out

    loaded = load_city("yerevan")
    city = loaded.model_copy(
        update={
            "articles": [article.model_copy(update={"sources": []}) for article in loaded.articles],
            "hdd_sources": [],
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


def test_run_defaults_to_the_previous_calendar_month(tmp_path) -> None:
    seen: list[MonthOptions] = []

    def runner(options: MonthOptions) -> MonthReport:
        seen.append(options)
        return MonthReport(months=(), alerts=(), written=(), warnings=())

    code = main(
        ["--run", "--city", "yerevan", "--item", "gas", "--dry-run", "--data-root", str(tmp_path)],
        today=date(2027, 1, 7),
        runner=runner,
        env={"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": "owner/repo"},
    )

    assert code == 0
    (options,) = seen
    assert str(options.period) == "2026-12"
    assert [city.id.value for city in options.cities] == ["yerevan"]
    assert options.item is Item.gas
    assert options.dry_run is True
    assert options.data_root == tmp_path
    assert "token" in options.hide


def test_run_sends_alerts_only_with_a_token_and_a_repository(tmp_path) -> None:
    seen: list[MonthOptions] = []

    def runner(options: MonthOptions) -> MonthReport:
        seen.append(options)
        return MonthReport(months=(), alerts=(), written=(), warnings=())

    github = {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": "owner/repo"}
    for argv, env in (
        (["--run"], github),
        (["--run"], {"GITHUB_TOKEN": "token"}),
        (["--run", "--no-alerts"], github),
    ):
        assert main([*argv, "--data-root", str(tmp_path)], runner=runner, env=env) == 0

    assert [options.issues is not None for options in seen] == [True, False, False]


def test_run_exits_nonzero_and_names_every_problem(capsys) -> None:
    def runner(options: MonthOptions) -> MonthReport:
        raise PublicationError(["yerevan: нет курса за 2026-10", "berlin: нет тарифа на месяц"])

    assert main(["--run"], runner=runner, env={}) == 1

    err = capsys.readouterr().err
    assert "нет курса за 2026-10" in err and "нет тарифа на месяц" in err


def test_run_refuses_a_month_that_has_not_ended() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--run", "--period", "2026-11"], today=date(2026, 11, 25), env={})

    assert caught.value.code == 2
