"""Shared fixtures. Tests must not open sockets or launch curl/Chrome."""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import pytest

_BLOCKED_BINARIES = frozenset(
    {
        "curl",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "curl.exe",
        "chrome",
        "chrome.exe",
    }
)


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(socket, "create_connection", blocked)

    real_run = subprocess.run

    def guarded_run(
        args: object, *pos: object, **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        argv = list(args) if isinstance(args, (list, tuple)) else [args]
        if argv and Path(str(argv[0])).name in _BLOCKED_BINARIES:
            raise RuntimeError("network is disabled in tests")
        return real_run(args, *pos, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", guarded_run)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pauses between requests cost nothing in tests."""

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
