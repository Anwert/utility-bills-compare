"""Shared fixtures. Tests must not open sockets."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
