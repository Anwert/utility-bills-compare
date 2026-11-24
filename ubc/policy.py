"""Which configured source is published for one article, and which alerts that raises.

``choose_article`` is seam (c). It does not fetch pages and it does not talk to
GitHub. ``ubc.alerts.sync_alerts`` delivers the alerts it returns.

For one city article the enabled sources are walked with
``ordered_enabled_sources`` (kind, then priority, then file order). Disabled
sources are ignored even when an attempt is passed for them. A source with no
attempt was not consulted: it is neither a success nor an outage. The first
consulted source that returns a usable record for this city and article is the
candidate.

The candidate is published as-is when there is no previous value, or the value
moved by at most 30% (exactly 30% is not a jump). A move strictly greater than
30% is published only when a later source confirms it or the same jump was
already held last month. Otherwise the previous record stays, with ``Mark.stale``.

Confirmation: the later reading also moves more than 30% from the published
value, in the same direction, and the two new readings are not themselves more
than 30% apart. A repeat is the immediately next month (a skipped month does
not count) whose candidate is still a jump from the published value and agrees
with the held reading. The held reading is stored on ``ActingValue`` and
round-trips through ``dump_acting`` / ``load_acting``.

Every enabled source that was consulted and failed (error, empty result, or a
record that failed validation) raises one ``unavailable`` alert. A jump raises
one ``jump`` alert on the candidate's source whether or not the jump is
published. A source that returned a usable record and is not the jump source
raises a ``recovered`` alert so an open issue for that pair can be closed.
One alert per source. The sender keeps a single open issue per city, article,
and source.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ubc.schemas import (
    CityId,
    Item,
    Mark,
    NormalizedRecord,
    SourceConfig,
    YearMonth,
    ordered_enabled_sources,
)

# A jump is a change strictly greater than 30% of the previous published value.
_JUMP_NUMERATOR = 30
_JUMP_DENOMINATOR = 100

NOTE_JUMP = "скачок больше 30% не подтверждён, показано прошлое значение"
NOTE_UNAVAILABLE = "источники не отдали данные, показано прошлое значение"


class AttemptStatus(StrEnum):
    """Why a consulted source did not become the candidate, or that it did."""

    ok = "ok"
    error = "error"
    empty = "empty"
    invalid = "invalid"


class AlertReason(StrEnum):
    jump = "jump"
    unavailable = "unavailable"
    recovered = "recovered"


class AlertAction(StrEnum):
    """``open`` ensures one issue; ``resolve`` comments and closes it if it is open."""

    open = "open"
    resolve = "resolve"


@dataclass(frozen=True, slots=True)
class SourceAttempt:
    """What one configured source returned for this article this month.

    ``status=ok`` needs a ``record``. Any other status, a record for a different
    article or city, or a missing record counts as that source refusing.
    """

    source: SourceConfig
    status: AttemptStatus
    record: NormalizedRecord | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ActingValue:
    """The value currently on the site, plus a jump held back last month."""

    record: NormalizedRecord
    period: YearMonth
    source_id: str
    pending_record: NormalizedRecord | None = None
    pending_period: YearMonth | None = None
    pending_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class DataAlert:
    """One source's notification for one month. The sender deduplicates open issues."""

    city: CityId
    item: Item
    source_id: str
    reason: AlertReason
    action: AlertAction
    period: YearMonth
    old_value: float | None
    new_value: float | None
    unit: str | None
    url: str | None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ArticleDecision:
    """The record to feed the cell calculation, and the alerts for this article.

    ``record`` is ``None`` only when nothing was ever published and every
    consulted source failed. ``stale`` and ``marks`` are set together: a held
    previous value (unconfirmed jump or total outage) carries ``Mark.stale``.
    ``note`` is the Russian sentence a line can show next to that mark.
    ``acting`` is what the next month should receive as ``previous``.
    """

    record: NormalizedRecord | None
    stale: bool
    marks: tuple[Mark, ...]
    source_id: str | None
    note: str
    alerts: tuple[DataAlert, ...]
    acting: ActingValue | None


def choose_article(
    *,
    city: CityId,
    item: Item,
    period: YearMonth,
    sources: Sequence[SourceConfig],
    attempts: Sequence[SourceAttempt],
    previous: ActingValue | None = None,
) -> ArticleDecision:
    """Pick the published record for one article and the alerts that go with it."""

    enabled = ordered_enabled_sources(list(sources))
    by_id: dict[str, SourceAttempt] = {}
    for attempt in attempts:
        by_id.setdefault(attempt.source.id, attempt)

    failures: list[tuple[SourceConfig, SourceAttempt]] = []
    successes: dict[str, NormalizedRecord] = {}
    winner: tuple[SourceConfig, NormalizedRecord] | None = None
    later: list[NormalizedRecord] = []
    for source in enabled:
        attempt = by_id.get(source.id)
        if attempt is None:
            continue
        record = _usable(attempt, city, item)
        if record is None:
            failures.append((source, attempt))
            continue
        successes[source.id] = record
        if winner is None:
            winner = (source, record)
        else:
            later.append(record)

    if winner is None:
        return _unavailable(
            city=city,
            item=item,
            period=period,
            enabled=enabled,
            failures=failures,
            successes=successes,
            previous=previous,
        )

    source, record = winner
    if previous is None or not _jumped(previous.record.value, record.value):
        return _publish(
            city=city,
            item=item,
            period=period,
            enabled=enabled,
            failures=failures,
            successes=successes,
            previous=previous,
            source=source,
            record=record,
            jump=False,
        )

    confirmed = any(_confirms(previous.record.value, record.value, other.value) for other in later)
    if confirmed or _repeats(previous, period, record.value):
        return _publish(
            city=city,
            item=item,
            period=period,
            enabled=enabled,
            failures=failures,
            successes=successes,
            previous=previous,
            source=source,
            record=record,
            jump=True,
        )
    return _hold_jump(
        city=city,
        item=item,
        period=period,
        enabled=enabled,
        failures=failures,
        successes=successes,
        previous=previous,
        source=source,
        record=record,
    )


def dump_acting(acting: ActingValue) -> dict[str, Any]:
    """JSON-ready state for the next month. ``load_acting`` reads it back."""

    pending = acting.pending_record
    return {
        "record": acting.record.model_dump(mode="json"),
        "period": str(acting.period),
        "source_id": acting.source_id,
        "pending_record": pending.model_dump(mode="json") if pending is not None else None,
        "pending_period": str(acting.pending_period) if acting.pending_period is not None else None,
        "pending_source_id": acting.pending_source_id,
    }


def load_acting(data: Mapping[str, Any]) -> ActingValue:
    """Restore an ``ActingValue`` stored by ``dump_acting``."""

    pending_raw = data.get("pending_record")
    pending_period_raw = data.get("pending_period")
    pending_source_raw = data.get("pending_source_id")
    if pending_source_raw is not None and not isinstance(pending_source_raw, str):
        raise ValueError("pending_source_id must be a string")
    return ActingValue(
        record=NormalizedRecord.model_validate(data["record"]),
        period=YearMonth.parse(str(data["period"])),
        source_id=str(data["source_id"]),
        pending_record=NormalizedRecord.model_validate(pending_raw)
        if pending_raw is not None
        else None,
        pending_period=YearMonth.parse(str(pending_period_raw)) if pending_period_raw else None,
        pending_source_id=pending_source_raw,
    )


def _usable(attempt: SourceAttempt, city: CityId, item: Item) -> NormalizedRecord | None:
    if attempt.status is not AttemptStatus.ok or attempt.record is None:
        return None
    record = attempt.record
    if record.item is not item or record.city != city:
        return None
    return record


def _jumped(baseline: float, reading: float) -> bool:
    """True when ``reading`` differs from ``baseline`` by strictly more than 30%."""

    if baseline == 0:
        return reading != 0
    return abs(reading - baseline) * _JUMP_DENOMINATOR > abs(baseline) * _JUMP_NUMERATOR


def _agrees(left: float, right: float) -> bool:
    """True when neither reading is a jump away from the other."""

    return not _jumped(left, right) and not _jumped(right, left)


def _confirms(published: float, candidate: float, other: float) -> bool:
    if not _jumped(published, other):
        return False
    if (other - published) * (candidate - published) <= 0:
        return False
    return _agrees(candidate, other)


def _repeats(previous: ActingValue, period: YearMonth, candidate: float) -> bool:
    pending = previous.pending_record
    pending_period = previous.pending_period
    if pending is None or pending_period is None:
        return False
    if pending_period != _previous_month(period):
        return False
    if not _jumped(previous.record.value, candidate):
        return False
    return _agrees(pending.value, candidate)


def _previous_month(period: YearMonth) -> YearMonth:
    if period.month == 1:
        return YearMonth(period.year - 1, 12)
    return YearMonth(period.year, period.month - 1)


def _publish(
    *,
    city: CityId,
    item: Item,
    period: YearMonth,
    enabled: list[SourceConfig],
    failures: list[tuple[SourceConfig, SourceAttempt]],
    successes: dict[str, NormalizedRecord],
    previous: ActingValue | None,
    source: SourceConfig,
    record: NormalizedRecord,
    jump: bool,
) -> ArticleDecision:
    return ArticleDecision(
        record=record,
        stale=False,
        marks=(),
        source_id=source.id,
        note="",
        alerts=_alerts(
            city=city,
            item=item,
            period=period,
            enabled=enabled,
            failures=failures,
            successes=successes,
            previous=previous,
            jump_source_id=source.id if jump else None,
        ),
        acting=ActingValue(record=record, period=period, source_id=source.id),
    )


def _hold_jump(
    *,
    city: CityId,
    item: Item,
    period: YearMonth,
    enabled: list[SourceConfig],
    failures: list[tuple[SourceConfig, SourceAttempt]],
    successes: dict[str, NormalizedRecord],
    previous: ActingValue,
    source: SourceConfig,
    record: NormalizedRecord,
) -> ArticleDecision:
    return ArticleDecision(
        record=previous.record,
        stale=True,
        marks=(Mark.stale,),
        source_id=previous.source_id,
        note=NOTE_JUMP,
        alerts=_alerts(
            city=city,
            item=item,
            period=period,
            enabled=enabled,
            failures=failures,
            successes=successes,
            previous=previous,
            jump_source_id=source.id,
        ),
        acting=ActingValue(
            record=previous.record,
            period=previous.period,
            source_id=previous.source_id,
            pending_record=record,
            pending_period=period,
            pending_source_id=source.id,
        ),
    )


def _unavailable(
    *,
    city: CityId,
    item: Item,
    period: YearMonth,
    enabled: list[SourceConfig],
    failures: list[tuple[SourceConfig, SourceAttempt]],
    successes: dict[str, NormalizedRecord],
    previous: ActingValue | None,
) -> ArticleDecision:
    alerts = _alerts(
        city=city,
        item=item,
        period=period,
        enabled=enabled,
        failures=failures,
        successes=successes,
        previous=previous,
        jump_source_id=None,
    )
    if previous is None:
        return ArticleDecision(
            record=None,
            stale=False,
            marks=(),
            source_id=None,
            note="",
            alerts=alerts,
            acting=None,
        )
    return ArticleDecision(
        record=previous.record,
        stale=True,
        marks=(Mark.stale,),
        source_id=previous.source_id,
        note=NOTE_UNAVAILABLE,
        alerts=alerts,
        acting=ActingValue(
            record=previous.record,
            period=previous.period,
            source_id=previous.source_id,
        ),
    )


def _alerts(
    *,
    city: CityId,
    item: Item,
    period: YearMonth,
    enabled: list[SourceConfig],
    failures: list[tuple[SourceConfig, SourceAttempt]],
    successes: dict[str, NormalizedRecord],
    previous: ActingValue | None,
    jump_source_id: str | None,
) -> tuple[DataAlert, ...]:
    failed = {source.id: attempt for source, attempt in failures}
    old_value = previous.record.value if previous is not None else None
    old_unit = previous.record.unit if previous is not None else None
    alerts: list[DataAlert] = []
    for source in enabled:
        if source.id in failed:
            attempt = failed[source.id]
            alerts.append(
                DataAlert(
                    city=city,
                    item=item,
                    source_id=source.id,
                    reason=AlertReason.unavailable,
                    action=AlertAction.open,
                    period=period,
                    old_value=old_value,
                    new_value=None,
                    unit=old_unit,
                    url=_configured_url(source),
                    detail=attempt.detail or attempt.status.value,
                )
            )
            continue
        reading = successes.get(source.id)
        if reading is None:
            continue
        if source.id == jump_source_id:
            alerts.append(
                DataAlert(
                    city=city,
                    item=item,
                    source_id=source.id,
                    reason=AlertReason.jump,
                    action=AlertAction.open,
                    period=period,
                    old_value=old_value,
                    new_value=reading.value,
                    unit=reading.unit,
                    url=reading.source.url,
                )
            )
            continue
        alerts.append(
            DataAlert(
                city=city,
                item=item,
                source_id=source.id,
                reason=AlertReason.recovered,
                action=AlertAction.resolve,
                period=period,
                old_value=old_value,
                new_value=reading.value,
                unit=reading.unit,
                url=reading.source.url,
            )
        )
    return tuple(alerts)


def _configured_url(source: SourceConfig) -> str | None:
    url = source.settings.get("url")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url
    return None
