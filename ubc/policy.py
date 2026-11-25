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
value, and the two new readings are not themselves more than 30% apart. A repeat
is the immediately next month (a skipped month does not count) whose candidate is
still a jump from the published value and agrees with the held reading. The held
reading is stored on ``ActingValue`` and round-trips through ``dump_acting`` /
``load_acting``. An outage between the two readings does not drop it.

The whole tariff is compared, not just ``value``: every step, the unit, the
currency, and the other records the source bills (``others``). Prices are
brought to one unit first, so ``AMD/kWh`` and ``local/kWh``, or a yearly fee and
its twelfth, are the same price. A record that appeared or vanished is a jump
only when the source is the same one: a fallback may lack the standing fee.

Every enabled source that was consulted and failed (error, empty result, or a
record that failed validation) raises one ``unavailable`` alert. A jump raises
one ``jump`` alert on the candidate's source whether or not the jump is
published. A source that returned a usable record and is not the jump source
raises a ``recovered`` alert so an open issue for that pair can be closed.
One alert per source. A rerun of the month in which a jump was published sends no
``recovered``: the owner still has that jump to check. The sender keeps a single
open issue per city, article, and source.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ubc.schemas import (
    CityId,
    Currency,
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

    ``others`` are the rest of the records of this article that the source bills,
    for example a standing fee. ``status=ok`` needs a ``record``. Any other status,
    a record for a different article or city, or a missing record counts as that
    source refusing.
    """

    source: SourceConfig
    status: AttemptStatus
    record: NormalizedRecord | None = None
    detail: str = ""
    others: tuple[NormalizedRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ActingValue:
    """The value currently on the site, plus a jump held back last month.

    ``others`` are billed together with ``record``. ``jump_period`` is the month in
    which a jump was published, while that month can still be rerun.
    """

    record: NormalizedRecord
    period: YearMonth
    source_id: str
    pending_record: NormalizedRecord | None = None
    pending_period: YearMonth | None = None
    pending_source_id: str | None = None
    others: tuple[NormalizedRecord, ...] = ()
    pending_others: tuple[NormalizedRecord, ...] = ()
    jump_period: YearMonth | None = None


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
    winner: tuple[SourceConfig, _Tariff] | None = None
    later: list[_Tariff] = []
    for source in enabled:
        attempt = by_id.get(source.id)
        if attempt is None:
            continue
        record = _usable(attempt, city, item)
        if record is None:
            failures.append((source, attempt))
            continue
        successes[source.id] = record
        tariff = (record, *attempt.others)
        if winner is None:
            winner = (source, tariff)
        else:
            later.append(tariff)

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

    source, tariff = winner
    published: _Tariff = ()
    moved: str | None = None
    if previous is not None:
        published = (previous.record, *previous.others)
        moved = _moved(published, tariff, same_source=previous.source_id == source.id)
    if previous is None or moved is None:
        return _publish(
            city=city,
            item=item,
            period=period,
            enabled=enabled,
            failures=failures,
            successes=successes,
            previous=previous,
            source=source,
            tariff=tariff,
            moved=None,
        )

    confirmed = any(_confirms(published, tariff, other) for other in later)
    if confirmed or _repeats(previous, period, source, tariff):
        return _publish(
            city=city,
            item=item,
            period=period,
            enabled=enabled,
            failures=failures,
            successes=successes,
            previous=previous,
            source=source,
            tariff=tariff,
            moved=moved,
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
        tariff=tariff,
        moved=moved,
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
        "others": [record.model_dump(mode="json") for record in acting.others],
        "pending_others": [record.model_dump(mode="json") for record in acting.pending_others],
        "jump_period": str(acting.jump_period) if acting.jump_period is not None else None,
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
        others=_load_records(data.get("others")),
        pending_others=_load_records(data.get("pending_others")),
        jump_period=YearMonth.parse(str(data["jump_period"])) if data.get("jump_period") else None,
    )


def _load_records(raw: Any) -> tuple[NormalizedRecord, ...]:
    return tuple(NormalizedRecord.model_validate(record) for record in raw or ())


def _usable(attempt: SourceAttempt, city: CityId, item: Item) -> NormalizedRecord | None:
    if attempt.status is not AttemptStatus.ok or attempt.record is None:
        return None
    record = attempt.record
    if record.item is not item or record.city != city:
        return None
    return record


# One source's records of an article, the one that represents it first.
_Tariff = tuple[NormalizedRecord, ...]

_SUBUNITS = {
    "tetri": Currency.GEL,
    "ct": Currency.EUR,
    "eurocent": Currency.EUR,
    "grosz": Currency.PLN,
    "gr": Currency.PLN,
}
_KWH_TO_GJ = Decimal("0.0036")
_FUELS = frozenset({"kwh", "gj", "m3"})
_FEE_PARTS = frozenset({"m2", "person", "dwelling", "month", "year"})


@dataclass(frozen=True, slots=True)
class _Price:
    """A record's price in comparable units: the same measure means the same scale."""

    measure: tuple[str, ...]
    steps: tuple[tuple[Decimal | None, Decimal], ...]

    def at(self, upper: Decimal | None) -> Decimal:
        """The price of the consumption just below ``upper``; ``None`` is beyond every step."""

        for bound, price in self.steps:
            if bound is None or (upper is not None and upper <= bound):
                return price
        return self.steps[-1][1]


def _price(record: NormalizedRecord) -> _Price:
    head, *parts = [part.strip().lower() for part in record.unit.split("/")]
    currency = record.currency.value.lower() if record.currency is not None else ""
    fuels = [part for part in parts if part in _FUELS]
    rest = [part for part in parts if part not in _FUELS]
    scale = bound_scale = Decimal(1)
    if head in _SUBUNITS:
        scale = Decimal("0.01")
        known = record.currency is _SUBUNITS[head]
    else:
        known = head in {"local", currency}
    if not known or len(fuels) > 1 or not _FEE_PARTS.issuperset(rest):
        measure: tuple[str, ...] = (currency, record.unit.lower())
        scale = Decimal(1)
    elif fuels:
        if fuels == ["gj"]:
            scale, bound_scale = scale * _KWH_TO_GJ, 1 / _KWH_TO_GJ
        measure = (currency, "kwh" if fuels == ["gj"] else fuels[0])
    else:
        if "year" in rest:
            scale /= 12
        per = {"month" if part == "year" else part for part in rest} - {"dwelling"}
        measure = (currency, *sorted(per))
    tiers = [(tier.up_to, tier.value) for tier in record.tiers] or [(None, record.value)]
    return _Price(
        measure,
        tuple(
            (
                Decimal(str(bound)) * bound_scale if bound is not None else None,
                Decimal(str(value)) * scale,
            )
            for bound, value in tiers
        ),
    )


def _moved(old: _Tariff, new: _Tariff, *, same_source: bool) -> str | None:
    """What moved by strictly more than 30%. ``None`` when nothing did."""

    for record in new:
        base = _counterpart(old, record)
        if base is None:
            if same_source or record is new[0]:
                return f"появилась цена {_words(record)}, раньше было: {_list(old)}"
        elif _price_moved(_price(base), _price(record)):
            return f"было {_words(base)}, стало {_words(record)}"
    if same_source:
        for record in old:
            if _counterpart(new, record) is None:
                return f"пропала цена {_words(record)}, осталось: {_list(new)}"
    return None


def _counterpart(tariff: _Tariff, record: NormalizedRecord) -> NormalizedRecord | None:
    """The price ``record`` replaces: of the same date, else the latest before it."""

    measure = _price(record).measure
    same = [other for other in tariff if _price(other).measure == measure]
    for other in same:
        if other.valid_from == record.valid_from:
            return other
    before = [other for other in same if other.valid_from < record.valid_from]
    if before:
        return max(before, key=lambda other: other.valid_from)
    return same[0] if same else None


def _price_moved(old: _Price, new: _Price) -> bool:
    bounds = {bound for price in (old, new) for bound, _ in price.steps}
    return any(_jumped(old.at(bound), new.at(bound)) for bound in {*bounds, None})


def _words(record: NormalizedRecord) -> str:
    prices = [tier.value for tier in record.tiers] or [record.value]
    return f"{' / '.join(f'{price:g}' for price in prices)} {record.unit}"


def _list(tariff: _Tariff) -> str:
    return ", ".join(_words(record) for record in tariff)


def _jumped(baseline: Decimal, reading: Decimal) -> bool:
    """True when ``reading`` differs from ``baseline`` by strictly more than 30%."""

    if baseline == 0:
        return reading != 0
    return abs(reading - baseline) * _JUMP_DENOMINATOR > abs(baseline) * _JUMP_NUMERATOR


def _agrees(left: _Tariff, right: _Tariff, *, same_source: bool) -> bool:
    """True when neither reading is a jump away from the other."""

    return (
        _moved(left, right, same_source=same_source) is None
        and _moved(right, left, same_source=same_source) is None
    )


def _confirms(published: _Tariff, candidate: _Tariff, other: _Tariff) -> bool:
    if _moved(published, other, same_source=False) is None:
        return False
    return _agrees(candidate, other, same_source=False)


def _repeats(
    previous: ActingValue, period: YearMonth, source: SourceConfig, candidate: _Tariff
) -> bool:
    pending = previous.pending_record
    if pending is None or previous.pending_period != _previous_month(period):
        return False
    return _agrees(
        (pending, *previous.pending_others),
        candidate,
        same_source=previous.pending_source_id == source.id,
    )


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
    tariff: _Tariff,
    moved: str | None,
) -> ArticleDecision:
    unsettled = (
        moved is None
        and previous is not None
        and previous.jump_period == period
        and previous.source_id == source.id
    )
    return ArticleDecision(
        record=tariff[0],
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
            jump_source_id=source.id if moved is not None else None,
            jump_detail=moved or "",
            unsettled_source_id=source.id if unsettled else None,
        ),
        acting=ActingValue(
            record=tariff[0],
            others=tariff[1:],
            period=period,
            source_id=source.id,
            jump_period=period if moved is not None or unsettled else None,
        ),
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
    tariff: _Tariff,
    moved: str,
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
            jump_detail=moved,
        ),
        acting=replace(
            previous,
            pending_record=tariff[0],
            pending_others=tariff[1:],
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
        # A held jump stays: ``_repeats`` looks at its month, so one that got too old is inert.
        acting=previous,
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
    jump_detail: str = "",
    unsettled_source_id: str | None = None,
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
                    detail=jump_detail,
                )
            )
            continue
        if source.id == unsettled_source_id:
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
