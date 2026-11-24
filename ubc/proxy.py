"""Proxy transport for a ``proxy_direct`` source.

The adapter does not change. ``fetcher_for_source`` hands it a fetcher that
sends every read through the configured proxy. Country comes from the source
settings when set, otherwise from ``PROXY_COUNTRY``.

The built-in transport refuses to open a connection. Tests and a later
deployment pass their own ``ProxyTransport``. This is not an anti-bot bypass:
a blocked response is still a failed source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ubc.fetch import USER_AGENT, ContentFetcher, FetchError, _default_command, _pdftotext
from ubc.schemas import SourceConfig, SourceKind
from ubc.secrets import SecretConfig, SourceUnavailable


@dataclass(frozen=True)
class ProxyEndpoint:
    """Where to send the request. The password is omitted from ``repr``."""

    host: str
    login: str
    country: str
    password: str = field(repr=False)


class ProxyTransport(Protocol):
    def fetch_bytes(self, url: str, *, proxy: ProxyEndpoint, user_agent: str) -> bytes: ...


class RefusingProxyTransport:
    """MVP default. Raises before any socket or proxy handshake."""

    def fetch_bytes(self, url: str, *, proxy: ProxyEndpoint, user_agent: str) -> bytes:
        del proxy, user_agent
        raise FetchError(
            url,
            "proxy transport is disabled in the MVP and does not open a connection",
        )


class ProxyFetcher:
    """``ContentFetcher`` that routes reads through one proxy endpoint."""

    def __init__(self, endpoint: ProxyEndpoint, transport: ProxyTransport) -> None:
        self.endpoint = endpoint
        self.transport = transport

    def fetch_bytes(self, url: str) -> bytes:
        return self.transport.fetch_bytes(url, proxy=self.endpoint, user_agent=USER_AGENT)

    def fetch_text(self, url: str) -> str:
        return self.fetch_bytes(url).decode("utf-8", "replace")

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        del budget_ms
        raise FetchError(url, "proxy transport is disabled for headless Chrome in the MVP")

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        body = self.fetch_bytes(url)
        return _pdftotext(body, layout=layout, url=url, command=_default_command)


def fetcher_for_source(
    source: SourceConfig,
    direct: ContentFetcher,
    secrets: SecretConfig,
    transport: ProxyTransport | None = None,
) -> ContentFetcher:
    """The fetcher an adapter should use for this source.

    An ordinary source keeps ``direct``. A proxy source without secrets raises
    ``SourceUnavailable`` so the policy can move on.
    """

    if source.kind is not SourceKind.proxy_direct:
        return direct
    if not secrets.proxy_ready:
        raise SourceUnavailable("proxy source is unavailable without proxy secrets")
    host = secrets.proxy_host
    login = secrets.proxy_login
    password = secrets.proxy_password
    fallback = secrets.proxy_country
    if host is None or login is None or password is None or fallback is None:
        raise SourceUnavailable("proxy source is unavailable without proxy secrets")
    configured = source.settings.get("country")
    if isinstance(configured, str) and configured.strip():
        country = configured.strip()
    else:
        country = fallback
    return ProxyFetcher(
        ProxyEndpoint(host=host, login=login, country=country, password=password),
        transport if transport is not None else RefusingProxyTransport(),
    )
