"""Source order, the 30% publication hold, and data-alert issues (seam c)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from email.message import EmailMessage

import pytest

from ubc.alerts import (
    AlertDeliveryError,
    GitHubRestClient,
    IssueSnapshot,
    github_client_from_env,
    sync_alerts,
)
from ubc.policy import (
    ActingValue,
    AlertAction,
    AlertReason,
    ArticleDecision,
    AttemptStatus,
    DataAlert,
    SourceAttempt,
    choose_article,
    dump_acting,
    load_acting,
)
from ubc.schemas import (
    Category,
    CityId,
    Currency,
    ExtractionMethod,
    Item,
    Mark,
    NormalizedRecord,
    Reliability,
    SourceConfig,
    SourceInfo,
    SourceKind,
    YearMonth,
)


def _source(
    source_id: str,
    kind: str,
    *,
    priority: int = 0,
    enabled: bool = True,
    url: str | None = None,
) -> SourceConfig:
    reliability = {
        "proxy_direct": "official",
        "official": "official",
        "third_party": "third_party",
        "statistics": "statistics",
    }[kind]
    settings = {"url": url} if url else {}
    return SourceConfig(
        id=source_id,
        adapter="example_html",
        kind=kind,
        priority=priority,
        enabled=enabled,
        reliability=reliability,
        settings=settings,
    )


def _record(value: float, url: str, kind: str = "official") -> NormalizedRecord:
    if kind == "proxy_direct":
        source_kind = SourceKind.proxy_direct
        reliability = Reliability.official
    else:
        source_kind = SourceKind(kind)
        reliability = Reliability(kind)
    return NormalizedRecord(
        city=CityId.yerevan,
        item=Item.waste,
        category=Category.utility,
        value=value,
        unit="AMD/m2/month",
        currency=Currency.AMD,
        valid_from="2026-01-01",
        source=SourceInfo(
            kind=source_kind,
            url=url,
            fetched_at="2026-11-01T00:00:00+00:00",
        ),
        method=ExtractionMethod.regex,
        reliability=reliability,
    )


def _ok(source: SourceConfig, value: float, url: str) -> SourceAttempt:
    return SourceAttempt(
        source=source,
        status=AttemptStatus.ok,
        record=_record(value, url, source.kind.value),
    )


def _failed(source: SourceConfig, status: AttemptStatus, detail: str = "") -> SourceAttempt:
    return SourceAttempt(source=source, status=status, detail=detail)


def _published(value: float, url: str, period: str = "2026-09") -> ActingValue:
    return ActingValue(
        record=_record(value, url),
        period=YearMonth.parse(period),
        source_id="waste_official",
    )


def _choose(
    period: str,
    sources: Sequence[SourceConfig],
    attempts: Sequence[SourceAttempt],
    previous: ActingValue | None = None,
) -> ArticleDecision:
    return choose_article(
        city=CityId.yerevan,
        item=Item.waste,
        period=YearMonth.parse(period),
        sources=sources,
        attempts=attempts,
        previous=previous,
    )


def _opened(decision: ArticleDecision) -> list[tuple[str, AlertReason, float | None, float | None]]:
    return [
        (alert.source_id, alert.reason, alert.old_value, alert.new_value)
        for alert in decision.alerts
        if alert.action is AlertAction.open
    ]


def test_fallback_skips_a_failed_higher_priority_source() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    stats = _source("waste_stats", "statistics", url="https://example.test/stats")

    decision = _choose(
        "2026-10",
        [stats, third, official],
        [
            _ok(stats, 9, "https://example.test/stats"),
            _ok(third, 7, "https://example.test/third"),
            _failed(official, AttemptStatus.invalid, "schema"),
        ],
    )

    assert decision.record is not None
    assert decision.record.value == 7
    assert decision.record.source.url == "https://example.test/third"
    assert decision.source_id == "waste_third"
    assert decision.stale is False
    assert decision.marks == ()
    assert _opened(decision) == [("waste_official", AlertReason.unavailable, None, None)]


def test_disabled_source_is_not_consulted() -> None:
    proxy = _source(
        "waste_proxy",
        "proxy_direct",
        enabled=False,
        url="https://example.test/proxy",
    )
    official = _source("waste_official", "official", url="https://example.test/official")

    decision = _choose(
        "2026-10",
        [proxy, official],
        [
            _ok(proxy, 1, "https://example.test/proxy"),
            _ok(official, 2, "https://example.test/official"),
        ],
    )

    assert decision.record is not None
    assert decision.record.value == 2
    assert decision.record.source.url == "https://example.test/official"
    assert decision.stale is False
    assert all(alert.source_id != "waste_proxy" for alert in decision.alerts)


def test_change_of_exactly_30_percent_is_published_from_the_first_source() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    stats = _source("waste_stats", "statistics", url="https://example.test/stats")
    previous = _published(100, "https://example.test/previous")

    decision = _choose(
        "2026-10",
        [stats, official],
        [
            _ok(stats, 1, "https://example.test/stats"),
            _ok(official, 130, "https://example.test/official"),
        ],
        previous,
    )

    assert decision.record is not None
    assert decision.record.value == 130
    assert decision.record.source.url == "https://example.test/official"
    assert decision.stale is False
    assert decision.marks == ()
    assert _opened(decision) == []


def test_jump_over_30_percent_alerts_and_keeps_the_previous_value() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    previous = _published(100, "https://example.test/previous")

    decision = _choose(
        "2026-10",
        [third, official],
        [
            _ok(official, 140, "https://example.test/official"),
            _ok(third, 100, "https://example.test/third"),
        ],
        previous,
    )

    assert decision.record is not None
    assert decision.record.value == 100
    assert decision.record.source.url == "https://example.test/previous"
    assert decision.stale is True
    assert decision.marks == (Mark.stale,)
    assert decision.source_id == "waste_official"
    assert _opened(decision) == [("waste_official", AlertReason.jump, 100, 140)]
    assert decision.acting is not None
    assert decision.acting.record.value == 100
    assert str(decision.acting.period) == "2026-09"
    assert decision.acting.pending_record is not None
    assert decision.acting.pending_record.value == 140
    assert str(decision.acting.pending_period) == "2026-10"
    assert decision.acting.pending_source_id == "waste_official"
    jump = decision.alerts[0]
    assert jump.url == "https://example.test/official"


def test_second_source_confirmation_publishes_the_jump() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    previous = _published(100, "https://example.test/previous")

    decision = _choose(
        "2026-10",
        [official, third],
        [
            _ok(official, 140, "https://example.test/official"),
            _ok(third, 150, "https://example.test/third"),
        ],
        previous,
    )

    assert decision.record is not None
    assert decision.record.value == 140
    assert decision.record.source.url == "https://example.test/official"
    assert decision.stale is False
    assert decision.marks == ()
    assert decision.source_id == "waste_official"
    assert _opened(decision) == [("waste_official", AlertReason.jump, 100, 140)]
    assert decision.acting is not None
    assert decision.acting.pending_record is None


def test_jump_that_repeats_next_month_is_published() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    held = _choose(
        "2026-10",
        [official, third],
        [
            _ok(official, 140, "https://example.test/official"),
            _ok(third, 100, "https://example.test/third"),
        ],
        _published(100, "https://example.test/previous"),
    )
    assert held.acting is not None

    decision = _choose(
        "2026-11",
        [official, third],
        [
            _ok(official, 140, "https://example.test/official"),
            _ok(third, 100, "https://example.test/third"),
        ],
        load_acting(dump_acting(held.acting)),
    )

    assert decision.record is not None
    assert decision.record.value == 140
    assert decision.record.source.url == "https://example.test/official"
    assert decision.stale is False
    assert decision.marks == ()
    assert decision.acting is not None
    assert decision.acting.pending_record is None
    assert str(decision.acting.period) == "2026-11"


def test_jump_after_a_skipped_month_stays_on_hold() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    held = _choose(
        "2026-10",
        [official, third],
        [
            _ok(official, 140, "https://example.test/official"),
            _ok(third, 100, "https://example.test/third"),
        ],
        _published(100, "https://example.test/previous"),
    )
    assert held.acting is not None

    decision = _choose(
        "2026-12",
        [official, third],
        [
            _ok(official, 140, "https://example.test/official"),
            _ok(third, 100, "https://example.test/third"),
        ],
        held.acting,
    )

    assert decision.record is not None
    assert decision.record.value == 100
    assert decision.record.source.url == "https://example.test/previous"
    assert decision.stale is True
    assert decision.marks == (Mark.stale,)
    assert decision.acting is not None
    assert decision.acting.pending_record is not None
    assert decision.acting.pending_record.value == 140
    assert str(decision.acting.pending_period) == "2026-12"


def test_all_sources_failing_keeps_the_previous_value_and_marks_it_stale() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    previous = ActingValue(
        record=_record(80, "https://example.test/previous"),
        period=YearMonth.parse("2026-09"),
        source_id="waste_official",
        pending_record=_record(140, "https://example.test/official"),
        pending_period=YearMonth.parse("2026-09"),
        pending_source_id="waste_official",
    )

    decision = _choose(
        "2026-10",
        [official, third],
        [
            _failed(official, AttemptStatus.empty),
            _failed(third, AttemptStatus.error, "timeout"),
        ],
        previous,
    )

    assert decision.record is not None
    assert decision.record.value == 80
    assert decision.record.source.url == "https://example.test/previous"
    assert decision.stale is True
    assert decision.marks == (Mark.stale,)
    assert decision.source_id == "waste_official"
    assert _opened(decision) == [
        ("waste_official", AlertReason.unavailable, 80, None),
        ("waste_third", AlertReason.unavailable, 80, None),
    ]
    assert decision.acting is not None
    # A rerun of this month may still confirm the jump held in September.
    assert decision.acting.pending_record is not None
    assert str(decision.acting.pending_period) == "2026-09"
    assert [alert.url for alert in decision.alerts if alert.action is AlertAction.open] == [
        "https://example.test/official",
        "https://example.test/third",
    ]


class _StoredIssue:
    def __init__(self, number: int, title: str, body: str, labels: list[str]) -> None:
        self.number = number
        self.title = title
        self.body = body
        self.state = "open"
        self.labels = labels
        self.comments: list[str] = []


class FakeGitHub:
    """In-memory GitHub issues. Refuses a label that was never created."""

    def __init__(self) -> None:
        self.labels: dict[str, str] = {}
        self.issues: list[_StoredIssue] = []

    def ensure_label(self, name: str, color: str, description: str) -> None:
        self.labels.setdefault(name, description)

    def list_open_issues(self, label: str) -> list[IssueSnapshot]:
        return [
            IssueSnapshot(issue.number, issue.title, issue.body, issue.state, tuple(issue.labels))
            for issue in self.issues
            if issue.state == "open" and label in issue.labels
        ]

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> IssueSnapshot:
        missing = [label for label in labels if label not in self.labels]
        if missing:
            raise RuntimeError(f"unknown label {missing[0]}")
        issue = _StoredIssue(len(self.issues) + 1, title, body, list(labels))
        self.issues.append(issue)
        return IssueSnapshot(
            issue.number, issue.title, issue.body, issue.state, tuple(issue.labels)
        )

    def comment(self, number: int, body: str) -> None:
        self._issue(number).comments.append(body)

    def close_issue(self, number: int) -> None:
        self._issue(number).state = "closed"

    def _issue(self, number: int) -> _StoredIssue:
        for issue in self.issues:
            if issue.number == number:
                return issue
        raise KeyError(number)


def test_repeated_outage_does_not_open_another_issue() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    decision = _choose(
        "2026-10",
        [official, third],
        [
            _failed(official, AttemptStatus.empty),
            _failed(third, AttemptStatus.invalid, "schema"),
        ],
        _published(80, "https://example.test/previous"),
    )
    github = FakeGitHub()

    sync_alerts(github, decision.alerts)
    sync_alerts(github, decision.alerts)

    assert len(github.issues) == 2
    assert [issue.state for issue in github.issues] == ["open", "open"]
    assert [issue.comments for issue in github.issues] == [[], []]
    assert [issue.labels for issue in github.issues] == [["data-alert"], ["data-alert"]]
    bodies = [issue.body for issue in github.issues]
    assert any("waste_official" in body and "80" in body for body in bodies)
    assert any("waste_third" in body for body in bodies)
    assert "https://example.test/official" in bodies[0]
    assert "https://example.test/third" in bodies[1]


def test_sync_opens_a_data_alert_and_closes_it_when_the_reading_recovers() -> None:
    official = _source("waste_official", "official", url="https://example.test/official")
    third = _source("waste_third", "third_party", url="https://example.test/third")
    held = _choose(
        "2026-10",
        [official, third],
        [
            _ok(official, 140, "https://example.test/official"),
            _ok(third, 100, "https://example.test/third"),
        ],
        _published(100, "https://example.test/previous"),
    )
    github = FakeGitHub()

    sync_alerts(github, held.alerts)
    sync_alerts(github, held.alerts)

    assert list(github.labels) == ["data-alert"]
    assert len(github.issues) == 1
    issue = github.issues[0]
    assert issue.state == "open"
    assert issue.labels == ["data-alert"]
    assert issue.comments == []
    assert issue.title.startswith("data-alert:")
    assert "waste_official" in issue.title
    assert "Ереван" in issue.body
    assert "Мусор" in issue.body
    assert "waste_official" in issue.body
    assert "100" in issue.body
    assert "140" in issue.body
    assert "https://example.test/official" in issue.body
    assert "30%" in issue.body

    assert held.acting is not None
    recovered = _choose(
        "2026-11",
        [official, third],
        [
            _ok(official, 110, "https://example.test/official"),
            _ok(third, 110, "https://example.test/third"),
        ],
        load_acting(dump_acting(held.acting)),
    )
    assert recovered.record is not None
    assert recovered.record.value == 110
    assert recovered.stale is False

    sync_alerts(github, recovered.alerts)

    assert issue.state == "closed"
    assert len(github.issues) == 1
    assert len(issue.comments) == 1
    assert "110" in issue.comments[0]
    assert "Задача закрыта." in issue.comments[0]
    assert "waste_official" in issue.comments[0]


class _HttpResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _HttpResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _RecordingOpener:
    """Stands in for urllib. Records calls and never opens a socket."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes | None]] = []

    def open(self, request: urllib.request.Request, *, timeout: float = 30) -> _HttpResponse:
        del timeout
        raw = request.data if isinstance(request.data, bytes) else None
        method = request.get_method()
        self.calls.append((method, request.full_url, raw))
        if request.get_header("Authorization") != "Bearer actions-token":
            raise AssertionError("token must travel in the Authorization header")
        if method == "GET" and request.full_url.endswith("/labels/data-alert"):
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                EmailMessage(),
                io.BytesIO(b"missing"),
            )
        if method == "POST" and request.full_url.endswith("/labels"):
            return _HttpResponse(201, {"name": "data-alert"})
        if method == "GET" and "/issues?" in request.full_url:
            return _HttpResponse(200, [])
        if method == "POST" and request.full_url.endswith("/issues"):
            posted = json.loads(raw or b"{}")
            return _HttpResponse(
                201,
                {
                    "number": 7,
                    "title": posted["title"],
                    "body": posted["body"],
                    "state": "open",
                    "labels": [{"name": label} for label in posted["labels"]],
                },
            )
        raise AssertionError(f"unexpected {method} {request.full_url}")


def test_rest_client_creates_the_label_and_the_issue_without_a_proxy() -> None:
    opener = _RecordingOpener()
    client = GitHubRestClient("acme", "bills", "actions-token", opener=opener)
    alert = DataAlert(
        city=CityId.yerevan,
        item=Item.waste,
        source_id="waste_official",
        reason=AlertReason.jump,
        action=AlertAction.open,
        period=YearMonth.parse("2026-10"),
        old_value=100,
        new_value=140,
        unit="AMD/m2/month",
        url="https://example.test/official",
    )

    sync_alerts(client, [alert])

    methods = [(method, url) for method, url, _body in opener.calls]
    assert methods == [
        ("GET", "https://api.github.com/repos/acme/bills/labels/data-alert"),
        ("POST", "https://api.github.com/repos/acme/bills/labels"),
        (
            "GET",
            "https://api.github.com/repos/acme/bills/issues"
            "?state=open&labels=data-alert&per_page=100&page=1",
        ),
        ("POST", "https://api.github.com/repos/acme/bills/issues"),
    ]
    label = json.loads(opener.calls[1][2] or b"{}")
    issue = json.loads(opener.calls[3][2] or b"{}")
    assert label == {
        "name": "data-alert",
        "color": "b60205",
        "description": "Источник сломался или значение скакнуло больше чем на 30%",
    }
    assert issue["labels"] == ["data-alert"]
    assert issue["title"] == "data-alert: Ереван — Мусор — waste_official"
    assert "ubc-data-alert key=yerevan/waste/waste_official" in issue["body"]
    assert "100" in issue["body"]
    assert "140" in issue["body"]
    assert "https://example.test/official" in issue["body"]
    assert "actions-token" not in opener.calls[3][1]


def test_github_client_requires_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with pytest.raises(AlertDeliveryError):
        github_client_from_env("acme", "bills")


# What counts as a move of more than 30%


def _priced(value: float = 10, *, tiers: Sequence[tuple[float | None, float]] = (), **changes):
    data = _record(value, "https://example.test/official").model_dump(mode="json")
    data |= {"item": "electricity", "unit": "AMD/kWh"} | changes
    data["tiers"] = [{"up_to": up_to, "value": price} for up_to, price in tiers]
    return NormalizedRecord.model_validate(data)


def _power(
    published: Sequence[NormalizedRecord],
    answered: Sequence[NormalizedRecord],
    *,
    period: str = "2026-10",
    source: SourceConfig | None = None,
    previous: ActingValue | None = None,
) -> ArticleDecision:
    chosen = source or _source("power", "official")
    return choose_article(
        city=CityId.yerevan,
        item=Item.electricity,
        period=YearMonth.parse(period),
        sources=[chosen],
        attempts=[SourceAttempt(chosen, AttemptStatus.ok, answered[0], others=tuple(answered[1:]))],
        previous=previous
        or ActingValue(
            record=published[0],
            others=tuple(published[1:]),
            period=YearMonth.parse("2026-09"),
            source_id="power",
        ),
    )


STEPS = ((201, 10), (401, 11), (None, 12))
FEE = {"unit": "AMD/month"}


@pytest.mark.parametrize(
    ("published", "answered"),
    [
        ([_priced(tiers=STEPS)], [_priced(tiers=((201, 10), (401, 110), (None, 120)))]),
        ([_priced(tiers=STEPS)], [_priced(tiers=((201, 100), (401, 110), (None, 120)))]),
        ([_priced(tiers=STEPS)], [_priced(tiers=((201, 10), (None, 20)))]),
        ([_priced()], [_priced(tiers=((201, 10), (None, 20)))]),
        ([_priced()], [_priced(unit="local/kWh", currency="EUR")]),
        ([_priced()], [_priced(unit="AMD/GJ")]),
        ([_priced()], [_priced(unit="AMD/1000kWh")]),
        ([_priced()], [_priced(), _priced(500, **FEE)]),
        ([_priced(), _priced(500, **FEE)], [_priced(), _priced(700, **FEE)]),
        ([_priced(), _priced(500, **FEE)], [_priced()]),
        ([_priced()], [_priced(), _priced(20, valid_from="2026-10-15")]),
    ],
    ids=[
        "upper steps",
        "every step, same value",
        "a step is gone",
        "steps appeared",
        "currency",
        "energy unit",
        "unknown unit",
        "standing fee appeared",
        "standing fee moved",
        "standing fee is gone",
        "later price of the same source",
    ],
)
def test_a_jump_anywhere_in_the_tariff_is_held(
    published: list[NormalizedRecord], answered: list[NormalizedRecord]
) -> None:
    decision = _power(published, answered)

    assert decision.stale is True
    assert decision.record == published[0]
    (alert,) = decision.alerts
    assert alert.reason is AlertReason.jump
    assert alert.detail


@pytest.mark.parametrize(
    ("published", "answered"),
    [
        ([_priced()], [_priced(unit="local/kWh")]),
        ([_priced()], [_priced(unit="amd / KWH")]),
        ([_priced(36)], [_priced(10000, unit="AMD/GJ")]),
        ([_priced(120, **FEE)], [_priced(1440, unit="AMD/year")]),
        ([_priced(120, **FEE)], [_priced(120, unit="AMD/dwelling/month")]),
        (
            [_priced(0.2, unit="GEL/kWh", currency="GEL")],
            [_priced(20, unit="tetri/kWh", currency="GEL")],
        ),
        ([_priced(tiers=STEPS)], [_priced(tiers=((251, 10), (401, 11.5), (None, 12)))]),
        ([_priced()], [_priced(tiers=((201, 10), (None, 12)))]),
        (
            [_priced(), _priced(500, **FEE)],
            [_priced(500, **FEE), _priced()],
        ),
        ([_priced()], [_priced(), _priced(12, valid_from="2027-01-01")]),
        (
            [_priced(), _priced(20, valid_from="2027-01-01")],
            [_priced(20, valid_from="2027-01-01")],
        ),
    ],
    ids=[
        "local currency",
        "spelling",
        "GJ instead of kWh",
        "year instead of month",
        "dwelling spelled out",
        "subunit",
        "moved step bound",
        "steps close to the flat price",
        "order",
        "announced price",
        "announced price came into force",
    ],
)
def test_the_same_price_written_differently_is_not_a_jump(
    published: list[NormalizedRecord], answered: list[NormalizedRecord]
) -> None:
    decision = _power(published, answered)

    assert decision.stale is False
    assert decision.record == answered[0]


def test_a_fallback_without_the_standing_fee_is_not_a_jump_of_its_own() -> None:
    decision = _power(
        [_priced(), _priced(500, **FEE)], [_priced(11)], source=_source("other", "third_party")
    )

    assert decision.stale is False


def test_a_repeated_jump_of_the_standing_fee_is_published_only_if_it_is_the_same_one() -> None:
    published = [_priced(), _priced(500, **FEE)]
    held = _power(published, [_priced(), _priced(900, **FEE)])
    assert held.acting is not None
    previous = load_acting(json.loads(json.dumps(dump_acting(held.acting))))

    again = _power([], [_priced(), _priced(900, **FEE)], period="2026-11", previous=previous)
    other = _power([], [_priced(), _priced(9000, **FEE)], period="2026-11", previous=previous)

    assert again.stale is False
    assert other.stale is True


def test_state_saved_before_the_standing_fee_was_compared_still_loads() -> None:
    saved = dump_acting(
        ActingValue(record=_priced(), period=YearMonth.parse("2026-09"), source_id="power")
    )
    for key in ("others", "pending_others", "jump_period"):
        del saved[key]

    assert load_acting(saved).others == ()


# Reruns of the same month


@pytest.mark.parametrize("outage", ["2026-10", "2026-11"])
def test_an_outage_does_not_make_the_held_jump_start_over(outage: str) -> None:
    official = _source("waste_official", "official")
    jump = [_ok(official, 140, "https://example.test/official")]
    held = _choose("2026-10", [official], jump, _published(100, "https://example.test/previous"))
    broken = _choose(outage, [official], [_failed(official, AttemptStatus.error)], held.acting)
    assert broken.stale is True

    decision = _choose("2026-11", [official], jump, broken.acting)

    assert decision.record is not None
    assert decision.record.value == 140
    assert decision.stale is False


def test_rerun_after_a_confirmed_jump_leaves_its_alert_open_until_next_month() -> None:
    official = _source("waste_official", "official")
    jump = [_ok(official, 140, "https://example.test/official")]
    held = _choose("2026-10", [official], jump, _published(100, "https://example.test/previous"))
    confirmed = _choose("2026-11", [official], jump, held.acting)
    assert [alert.reason for alert in confirmed.alerts] == [AlertReason.jump]
    assert confirmed.acting is not None

    rerun = _choose("2026-11", [official], jump, load_acting(dump_acting(confirmed.acting)))
    later = _choose("2026-12", [official], jump, rerun.acting)

    assert rerun.stale is False
    assert rerun.alerts == ()
    assert [alert.reason for alert in later.alerts] == [AlertReason.recovered]
