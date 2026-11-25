"""From source answers to the records one article is billed by (seam c)."""

from __future__ import annotations

from datetime import date

import pytest

from ubc.articles import ArticleOutcome, Reading, decide_article, keep_article
from ubc.policy import AlertReason, AttemptStatus
from ubc.schemas import CityId, Item, Mark, NormalizedRecord, SourceConfig, YearMonth
from ubc.state import ArticleState

SOURCE = SourceConfig(id="power", adapter="example_html", kind="official", reliability="official")
DEAD = Reading(SOURCE, AttemptStatus.error, detail="timeout")


def price(value: float = 10, valid_from: str = "2026-01-01", **changes: object) -> NormalizedRecord:
    data: dict = {
        "city": "yerevan",
        "item": "electricity",
        "category": "utility",
        "value": value,
        "unit": "AMD/kWh",
        "currency": "AMD",
        "valid_from": valid_from,
        "source": {
            "kind": "official",
            "url": "https://example.test/power",
            "fetched_at": "2026-11-07T00:00:00+00:00",
        },
        "method": "regex",
        "reliability": "official",
    }
    return NormalizedRecord.model_validate(data | changes)


def answer(*records: NormalizedRecord) -> Reading:
    return Reading(SOURCE, AttemptStatus.ok, records)


def decide(month: str, reading: Reading, previous: ArticleState | None = None) -> ArticleOutcome:
    return decide_article(
        city=CityId.yerevan,
        item=Item.electricity,
        period=YearMonth.parse(month),
        sources=[SOURCE],
        readings=[reading],
        previous=previous,
    )


def after(*steps: tuple[str, Reading]) -> ArticleState:
    state = None
    for month, reading in steps:
        state = decide(month, reading, state).state
    assert state is not None
    return state


def billed(outcome: ArticleOutcome) -> list[tuple[float, date, date | None]]:
    return [(record.value, record.valid_from, record.valid_to) for record in outcome.records]


# Reruns and recounts


def test_outage_in_a_rerun_keeps_a_verified_month_and_still_alerts() -> None:
    state = after(("2026-10", answer(price())))

    outcome = decide("2026-10", DEAD, state)

    assert outcome.marks == ()
    assert outcome.state == state
    assert [alert.reason for alert in outcome.alerts] == [AlertReason.unavailable]


def test_outage_in_a_new_month_is_stale_and_stays_so_until_an_answer() -> None:
    state = after(("2026-10", answer(price())), ("2026-11", DEAD))

    assert decide("2026-11", DEAD, state).marks == (Mark.stale,)
    assert decide("2026-11", answer(price()), state).marks == ()


def test_a_jump_in_a_rerun_is_still_held() -> None:
    outcome = decide("2026-10", answer(price(20)), after(("2026-10", answer(price()))))

    assert outcome.marks == (Mark.stale,)
    assert billed(outcome) == [(10, date(2026, 1, 1), None)]


@pytest.mark.parametrize("reading", [DEAD, answer(price(20, "2026-11-20"))], ids=["outage", "jump"])
def test_recount_of_the_month_the_history_starts_in_is_not_stale(reading: Reading) -> None:
    state = after(("2026-11", answer(price(10, "2026-10-06"))))

    outcome = decide("2026-10", reading, state)

    assert outcome.marks == ()
    assert billed(outcome) == [(10, date(2026, 10, 1), None)]
    assert outcome.dated == {"https://example.test/power": date(2026, 10, 6)}
    assert outcome.state == state
    assert len(outcome.alerts) == 1


def test_a_month_before_any_known_price_reports_when_the_price_begins() -> None:
    state = after(("2026-11", answer(price(10, "2026-10-06"))))

    for outcome in (
        decide("2026-09", answer(price(10, "2026-10-06")), state),
        decide("2026-09", DEAD, state),
        decide("2026-09", answer(price(10, "2026-10-06"))),
        keep_article(state, YearMonth.parse("2026-09")),
    ):
        assert outcome.records == ()
        assert outcome.known_from == date(2026, 10, 6)


# Values that are not prices


@pytest.mark.parametrize(
    "record",
    [
        price(0),
        price(10, tiers=[{"up_to": 100, "value": 10}, {"up_to": None, "value": 0}]),
    ],
)
def test_a_price_that_is_not_positive_is_a_refusal(record: NormalizedRecord) -> None:
    first = decide("2026-10", answer(record))
    state = after(("2026-10", answer(price())), ("2026-11", answer(record)))

    assert first.records == ()
    (alert,) = first.alerts
    assert "не больше нуля" in alert.detail
    # The same zero a month later does not confirm itself.
    assert billed(decide("2026-12", answer(record), state)) == [(10, date(2026, 1, 1), None)]


def test_a_free_standing_fee_next_to_a_price_is_a_refusal_too() -> None:
    outcome = decide("2026-10", answer(price(), price(0, unit="AMD/month")))

    assert outcome.records == ()


# Which records are billed


def test_the_second_price_of_one_measure_and_date_is_not_billed() -> None:
    outcome = decide("2026-10", answer(price(46), price(36, unit="local/kWh")))

    assert [record.value for record in outcome.records] == [46]


def test_a_standing_fee_is_billed_next_to_the_price() -> None:
    outcome = decide("2026-10", answer(price(46), price(500, unit="AMD/month")))

    assert [record.value for record in outcome.records] == [46, 500]


def test_a_standing_fee_that_multiplied_is_held_with_the_whole_tariff() -> None:
    state = after(("2026-10", answer(price(46), price(500, unit="AMD/month"))))

    outcome = decide("2026-11", answer(price(46), price(5000, unit="AMD/month")), state)

    assert outcome.marks == (Mark.stale,)
    assert [record.value for record in outcome.records] == [46, 500]


def test_state_saved_before_other_records_were_kept_takes_them_from_the_history() -> None:
    state = after(("2026-10", answer(price(46), price(500, unit="AMD/month"))))
    acting = {key: value for key, value in state.acting.items() if key != "others"}
    old = ArticleState.model_validate(state.model_dump() | {"acting": acting})

    outcome = decide("2026-11", answer(price(46), price(500, unit="AMD/month")), old)

    assert outcome.marks == ()


def test_source_whose_tariff_ends_inside_the_month_is_a_refusal() -> None:
    outcome = decide("2026-10", answer(price(valid_to="2026-10-20")))

    assert outcome.records == ()
    assert "2026-10-20" in outcome.alerts[0].detail


def test_stale_month_extends_the_last_price_but_not_the_one_it_replaced() -> None:
    state = after(
        (
            "2026-10",
            answer(
                price(10, "2026-01-01", valid_to="2026-06-30"),
                price(11, "2026-07-01", valid_to="2026-10-31"),
            ),
        )
    )

    outcome = decide("2026-11", DEAD, state)

    assert billed(outcome) == [(11, date(2026, 7, 1), None)]


def test_replaced_price_ends_the_day_before_the_new_one() -> None:
    state = after(("2026-09", answer(price(10))))

    outcome = decide("2026-10", answer(price(12, "2026-10-17")), state)

    assert billed(outcome) == [
        (12, date(2026, 10, 17), None),
        (10, date(2026, 1, 1), date(2026, 10, 16)),
    ]
