"""Open and close ``data-alert`` GitHub issues. No network unless the client opens it.

``sync_alerts`` is the thin delivery layer for :func:`ubc.policy.choose_article`.
Tests pass a stand-in ``GitHubIssues``. GitHub Actions passes
``GitHubRestClient`` (or ``github_client_from_env``) with ``GITHUB_TOKEN``.
The decision of what to publish does not live here.

One open issue per city, article, and source. A repeated alert for a pair that
already has an open issue does not create another and does not add a comment.
A ``resolve`` alert comments and closes that issue when it is open.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from ubc.policy import AlertAction, AlertReason, DataAlert
from ubc.schemas import CITY_LABELS_RU, ITEM_LABELS_RU

DATA_ALERT_LABEL = "data-alert"
DATA_ALERT_COLOR = "b60205"
DATA_ALERT_DESCRIPTION = "Источник сломался или значение скакнуло больше чем на 30%"

_KEY = re.compile(r"ubc-data-alert key=(\S+)")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class AlertDeliveryError(RuntimeError):
    """GitHub refused the alert, or the Actions token is missing."""


@dataclass(frozen=True, slots=True)
class IssueSnapshot:
    """The fields the sender needs from an already-open issue."""

    number: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...]


class _UrlOpener(Protocol):
    """``urllib`` opener, or a test double with the same ``open`` method."""

    def open(self, request: urllib.request.Request, *, timeout: float = 30) -> Any: ...


class GitHubIssues(Protocol):
    """The slice of the GitHub API this layer uses. Tests substitute their own."""

    def ensure_label(self, name: str, color: str, description: str) -> None: ...

    def list_open_issues(self, label: str) -> Sequence[IssueSnapshot]: ...

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> IssueSnapshot: ...

    def comment(self, number: int, body: str) -> None: ...

    def close_issue(self, number: int) -> None: ...


def sync_alerts(client: GitHubIssues, alerts: Sequence[DataAlert]) -> None:
    """Create missing ``data-alert`` issues and close the ones that recovered.

    An alert whose pair already has an open issue is left untouched, so a
    broken source does not file a new issue every month. Recovery comments on
    the open issue and closes it.
    """

    if not alerts:
        return
    client.ensure_label(DATA_ALERT_LABEL, DATA_ALERT_COLOR, DATA_ALERT_DESCRIPTION)
    open_issues = _open_by_key(client)
    for alert in alerts:
        key = _alert_key(alert)
        existing = open_issues.get(key)
        if alert.action is AlertAction.open:
            if existing is None:
                title, body = _issue_text(alert)
                created = client.create_issue(title, body, (DATA_ALERT_LABEL,))
                open_issues[key] = created
            continue
        if alert.action is AlertAction.resolve and existing is not None:
            client.comment(existing.number, _recovery_text(alert))
            client.close_issue(existing.number)
            del open_issues[key]


class GitHubRestClient:
    """GitHub REST for one repository. Talks to ``api.github.com`` and no proxy.

    The Actions token is ``GITHUB_TOKEN`` (``github_client_from_env`` reads it).
    A 404 on the label means the label still has to be created. Any other
    error response raises ``AlertDeliveryError`` and does not publish a partial
    alert silently.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        opener: _UrlOpener | None = None,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._token = token
        self._api = api_url.rstrip("/")
        self._opener = opener if opener is not None else _OPENER

    def ensure_label(self, name: str, color: str, description: str) -> None:
        path = f"/repos/{self._owner}/{self._repo}/labels/{quote(name, safe='')}"
        status, _payload = self._request("GET", path)
        if status == 200:
            return
        if status != 404:
            raise AlertDeliveryError(f"GitHub label lookup for {name} returned {status}")
        created, _body = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/labels",
            {"name": name, "color": color, "description": description},
        )
        if created not in (200, 201):
            raise AlertDeliveryError(f"GitHub label create for {name} returned {created}")

    def list_open_issues(self, label: str) -> list[IssueSnapshot]:
        issues: list[IssueSnapshot] = []
        page = 1
        while page <= 20:
            status, payload = self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/issues"
                f"?state=open&labels={quote(label, safe='')}"
                f"&per_page=100&page={page}",
            )
            if status != 200 or not isinstance(payload, list):
                raise AlertDeliveryError(f"GitHub issue list returned {status}")
            if not payload:
                return issues
            for item in payload:
                if isinstance(item, dict) and "pull_request" not in item:
                    issues.append(_snapshot(item))
            if len(payload) < 100:
                return issues
            page += 1
        return issues

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> IssueSnapshot:
        status, payload = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/issues",
            {"title": title, "body": body, "labels": list(labels)},
        )
        if status not in (200, 201) or not isinstance(payload, dict):
            raise AlertDeliveryError(f"GitHub issue create returned {status}")
        return _snapshot(payload)

    def comment(self, number: int, body: str) -> None:
        status, _payload = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/issues/{number}/comments",
            {"body": body},
        )
        if status not in (200, 201):
            raise AlertDeliveryError(f"GitHub comment on issue {number} returned {status}")

    def close_issue(self, number: int) -> None:
        status, _payload = self._request(
            "PATCH",
            f"/repos/{self._owner}/{self._repo}/issues/{number}",
            {"state": "closed"},
        )
        if status not in (200, 201):
            raise AlertDeliveryError(f"GitHub close of issue {number} returned {status}")

    def _request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> tuple[int, object]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._api}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "utility-bills-compare",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read()
                parsed: object = json.loads(raw.decode("utf-8")) if raw else None
                return int(response.status), parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                return 404, detail
            raise AlertDeliveryError(
                f"GitHub {method} {path} returned {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AlertDeliveryError(f"GitHub {method} {path} failed: {exc.reason}") from exc


def github_client_from_env(owner: str, repo: str) -> GitHubRestClient:
    """Client for the repository that runs the monthly workflow."""

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        raise AlertDeliveryError("GITHUB_TOKEN is not set")
    return GitHubRestClient(owner, repo, token)


def _open_by_key(client: GitHubIssues) -> dict[str, IssueSnapshot]:
    found: dict[str, IssueSnapshot] = {}
    for issue in client.list_open_issues(DATA_ALERT_LABEL):
        if DATA_ALERT_LABEL not in issue.labels:
            continue
        matched = _KEY.search(issue.body)
        if matched:
            found[matched.group(1)] = issue
    return found


def _alert_key(alert: DataAlert) -> str:
    return f"{alert.city.value}/{alert.item.value}/{alert.source_id}"


def _issue_text(alert: DataAlert) -> tuple[str, str]:
    city = CITY_LABELS_RU[alert.city]
    item = ITEM_LABELS_RU[alert.item]
    title = f"data-alert: {city} — {item} — {alert.source_id}"
    if alert.reason is AlertReason.jump:
        reason = "значение изменилось больше чем на 30%"
    else:
        reason = "источник не отдал данные"
    if alert.detail:
        reason = f"{reason}: {alert.detail}"
    body = "\n".join(
        [
            f"<!-- ubc-data-alert key={_alert_key(alert)} -->",
            f"Город: {city}",
            f"Статья: {item}",
            f"Источник: {alert.source_id}",
            f"Старое значение: {_format_value(alert.old_value, alert.unit)}",
            f"Новое значение: {_format_value(alert.new_value, alert.unit)}",
            f"Ссылка: {alert.url or '—'}",
            f"Месяц: {alert.period}",
            f"Причина: {reason}",
        ]
    )
    return title, body


def _format_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return "—"
    number = str(int(value)) if float(value).is_integer() else format(value, "g")
    if unit:
        return f"{number} {unit}"
    return number


def _recovery_text(alert: DataAlert) -> str:
    return "\n".join(
        [
            f"Источник снова отдал данные за {alert.period}.",
            f"Город: {CITY_LABELS_RU[alert.city]}",
            f"Статья: {ITEM_LABELS_RU[alert.item]}",
            f"Источник: {alert.source_id}",
            f"Старое значение: {_format_value(alert.old_value, alert.unit)}",
            f"Новое значение: {_format_value(alert.new_value, alert.unit)}",
            f"Ссылка: {alert.url or '—'}",
            "Задача закрыта.",
        ]
    )


def _snapshot(item: dict[str, object]) -> IssueSnapshot:
    number = item.get("number")
    title = item.get("title")
    state = item.get("state")
    body = item.get("body") or ""
    if not isinstance(number, int) or not isinstance(title, str) or not isinstance(state, str):
        raise AlertDeliveryError("GitHub issue payload is missing number, title, or state")
    if not isinstance(body, str):
        body = ""
    return IssueSnapshot(number, title, body, state, _label_names(item.get("labels")))


def _label_names(labels: object) -> tuple[str, ...]:
    if not isinstance(labels, list):
        return ()
    names: list[str] = []
    for entry in labels:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str):
                names.append(name)
    return tuple(names)
