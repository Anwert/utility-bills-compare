"""From what the sources of one article returned to the records the month is billed by.

The selection policy compares one record per source. A source may return several
for one article, so this module decides which of them belong to the bill:

- records of another article or city are dropped;
- among records of the same measure (``AMD/kWh`` and ``local/kWh`` are one
  measure, ``AMD/month`` is another) with the same ``valid_from`` the first one
  stays. Adapters put the price of ordinary household use first, so the Yerevan
  night rate, which comes after the day rate, does not replace it;
- the first remaining record represents the source in the policy, which compares
  the others with it;
- a price of zero is not a tariff: such an answer is a refusal.

A value that was in force earlier fills only the days before the newer one starts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from ubc.policy import (
    AttemptStatus,
    DataAlert,
    SourceAttempt,
    choose_article,
    dump_acting,
)
from ubc.schemas import (
    CityId,
    Item,
    Mark,
    NormalizedRecord,
    SourceConfig,
    YearMonth,
)
from ubc.state import ArticleState, HistoryEntry

NOTE_NOT_COLLECTED = "статья в этом прогоне не собиралась, показано прошлое значение"


@dataclass(frozen=True, slots=True)
class Reading:
    """One source's answer. ``records`` is everything it returned, unfiltered."""

    source: SourceConfig
    status: AttemptStatus
    records: tuple[NormalizedRecord, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ArticleOutcome:
    """Records and badges for the calculation, alerts to send, state to keep.

    ``records`` is empty when nothing was ever published for the article, or when
    the earliest known price begins after the month: ``known_from`` is that date.
    ``dated`` maps a record stretched back to the first day of the month (by
    source URL) to the date the source itself gives.
    """

    item: Item
    records: tuple[NormalizedRecord, ...] = ()
    marks: tuple[Mark, ...] = ()
    alerts: tuple[DataAlert, ...] = ()
    state: ArticleState | None = None
    source_id: str | None = None
    dated: dict[str, date] = field(default_factory=dict)
    known_from: date | None = None


def decide_article(
    *,
    city: CityId,
    item: Item,
    period: YearMonth,
    sources: Sequence[SourceConfig],
    readings: Sequence[Reading],
    previous: ArticleState | None,
) -> ArticleOutcome:
    """Run the policy for one article and return what the month is billed by."""

    billable = {
        reading.source.id: billable_records(reading.records, city, item) for reading in readings
    }
    attempts = [
        _attempt(reading, billable[reading.source.id], period, first=previous is None)
        for reading in readings
    ]
    decision = choose_article(
        city=city,
        item=item,
        period=period,
        sources=sources,
        attempts=attempts,
        previous=previous.acting_value() if previous is not None else None,
    )
    if decision.record is None or decision.acting is None:
        answered = [record for records in billable.values() for record in records]
        return ArticleOutcome(
            item=item, alerts=decision.alerts, known_from=_starts_after(answered, period)
        )
    recount = previous is not None and _later(previous.checked_month(), period)
    if previous is not None and (
        (recount and (decision.stale or _covers(previous.history, period)))
        or _already_verified(previous, period, attempts)
    ):
        return replace(keep_article(previous, period), alerts=decision.alerts)
    history = list(previous.history) if previous is not None else []
    if not decision.stale and decision.source_id is not None:
        history = _append(history, period, decision.source_id, billable[decision.source_id])
    state = ArticleState(
        item=item,
        acting=dump_acting(decision.acting),
        checked=str(period),
        stale=decision.stale,
        note=decision.note,
        history=history,
    )
    outcome = _bill(state, period, stale=decision.stale, note=decision.note)
    if previous is not None and recount:
        # A recount of an older month must not move the policy state back in time.
        state = previous
    return replace(outcome, alerts=decision.alerts, state=state, source_id=decision.source_id)


def _already_verified(
    previous: ArticleState, period: YearMonth, attempts: Sequence[SourceAttempt]
) -> bool:
    """An earlier run of this month got the value, and this one got no answer at all."""

    return (
        previous.checked_month() == period
        and not previous.stale
        and all(attempt.status is not AttemptStatus.ok for attempt in attempts)
    )


def keep_article(state: ArticleState, period: YearMonth) -> ArticleOutcome:
    """The saved value of an article this run did not collect."""

    if state.checked_month() == period:
        return _bill(state, period, stale=state.stale, note=state.note)
    if _later(state.checked_month(), period):
        return _bill(state, period, stale=False, note="")
    return _bill(state, period, stale=True, note=NOTE_NOT_COLLECTED)


def billable_records(
    records: Sequence[NormalizedRecord], city: CityId, item: Item
) -> list[NormalizedRecord]:
    """Records of this article that belong to one household bill, adapter order kept."""

    kept: list[NormalizedRecord] = []
    seen: set[tuple[tuple[str, ...], date]] = set()
    for record in records:
        if record.item is not item or record.city != city:
            continue
        key = (_measure(record.unit), record.valid_from)
        if key in seen:
            continue
        seen.add(key)
        kept.append(record)
    return kept


def _measure(unit: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in unit.split("/")[1:])


def _attempt(
    reading: Reading,
    billable: list[NormalizedRecord],
    period: YearMonth,
    *,
    first: bool,
) -> SourceAttempt:
    if reading.status is not AttemptStatus.ok:
        return SourceAttempt(reading.source, reading.status, detail=reading.detail)
    if not billable:
        return SourceAttempt(
            reading.source, AttemptStatus.empty, detail="источник не вернул запись этой статьи"
        )
    if all(
        record.valid_to is not None and record.valid_to < period.last_day for record in billable
    ):
        ended = max(record.valid_to for record in billable if record.valid_to is not None)
        return SourceAttempt(
            reading.source,
            AttemptStatus.invalid,
            detail=f"тариф источника действует только по {ended}",
        )
    if any(price <= 0 for record in billable for price in _prices(record)):
        return SourceAttempt(reading.source, AttemptStatus.invalid, detail="цена не больше нуля")
    starts = min(record.valid_from for record in billable)
    if first and starts > period.last_day:
        return SourceAttempt(
            reading.source,
            AttemptStatus.invalid,
            detail=f"тариф источника действует с {starts}, позже месяца сбора",
        )
    return SourceAttempt(reading.source, AttemptStatus.ok, billable[0], others=tuple(billable[1:]))


def _prices(record: NormalizedRecord) -> list[float]:
    return [record.value, *(tier.value for tier in record.tiers)]


def _append(
    history: list[HistoryEntry],
    period: YearMonth,
    source_id: str,
    records: list[NormalizedRecord],
) -> list[HistoryEntry]:
    entry = HistoryEntry(period=str(period), source_id=source_id, records=records)
    if history and _content(history[-1]) == _content(entry):
        return history
    return [*history, entry]


def _content(entry: HistoryEntry) -> object:
    """What has to differ for the value to count as a new one."""

    return (
        entry.source_id,
        [
            record.model_dump(mode="json", exclude={"source": {"fetched_at"}})
            for record in entry.records
        ],
    )


def _bill(state: ArticleState, period: YearMonth, *, stale: bool, note: str) -> ArticleOutcome:
    records, dated = _month_records(state.history, period, extend=stale)
    if stale and note:
        records = [
            record.model_copy(update={"note": " ".join(filter(None, (record.note, note)))})
            for record in records
        ]
    return ArticleOutcome(
        item=state.item,
        records=tuple(records),
        marks=(Mark.stale,) if stale else (),
        state=state,
        source_id=state.history[-1].source_id,
        dated=dated,
        known_from=None
        if records
        else _starts_after([record for entry in state.history for record in entry.records], period),
    )


def _starts_after(records: Sequence[NormalizedRecord], period: YearMonth) -> date | None:
    """The date the earliest known price begins, when that is after the month."""

    earliest = min((record.valid_from for record in records), default=None)
    return earliest if earliest is not None and earliest > period.last_day else None


def _month_records(
    history: Sequence[HistoryEntry], period: YearMonth, *, extend: bool
) -> tuple[list[NormalizedRecord], dict[str, date]]:
    """Records in force during ``period``, newest first, without overlaps.

    ``extend`` keeps an expired last value in force: it is shown as stale.
    """

    first, last = period.first_day, period.last_day
    kept: list[NormalizedRecord] = []
    covered_from: date | None = None
    for entry in reversed(history):
        for record in entry.records:
            valid_to = record.valid_to
            if covered_from is None:
                if extend and not _renewed(record, entry.records):
                    valid_to = None
            elif valid_to is None or valid_to >= covered_from:
                valid_to = covered_from - timedelta(days=1)
            if valid_to is not None and (valid_to < record.valid_from or valid_to < first):
                continue
            if record.valid_from <= last:
                kept.append(record.model_copy(update={"valid_to": valid_to}))
        start = min(record.valid_from for record in entry.records)
        covered_from = start if covered_from is None else min(covered_from, start)
        if covered_from <= first:
            return kept, {}
    return _stretch_back(kept, first)


def _covers(history: Sequence[HistoryEntry], period: YearMonth) -> bool:
    """The saved values reach from the first day of the month to the last."""

    records, dated = _month_records(history, period, extend=False)
    return not dated and any(
        record.valid_to is None or record.valid_to >= period.last_day for record in records
    )


def _renewed(record: NormalizedRecord, records: Sequence[NormalizedRecord]) -> bool:
    """True when the same source also gives a later price of the same measure."""

    return any(
        other.valid_from > record.valid_from and _measure(other.unit) == _measure(record.unit)
        for other in records
    )


def _stretch_back(
    records: list[NormalizedRecord], first: date
) -> tuple[list[NormalizedRecord], dict[str, date]]:
    """The earliest known price begins inside the month: use it from the 1st, and say so."""

    earliest = min((record.valid_from for record in records), default=first)
    if earliest <= first:
        return records, {}
    note = (
        f"Источник датирует тариф {earliest}, более ранняя цена неизвестна: "
        "весь месяц посчитан по этой."
    )
    dated: dict[str, date] = {}
    stretched: list[NormalizedRecord] = []
    for record in records:
        if record.valid_from == earliest:
            dated[record.source.url] = earliest
            record = record.model_copy(
                update={"valid_from": first, "note": " ".join(filter(None, (record.note, note)))}
            )
        stretched.append(record)
    return stretched, dated


def _later(left: YearMonth, right: YearMonth) -> bool:
    return (left.year, left.month) > (right.year, right.month)
