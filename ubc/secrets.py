"""Secrets that turn the proxy transport and the LLM extractor on.

Both are off unless the process environment has the matching values. The
selection policy asks ``eligible_sources`` and skips whatever this module
reports as unavailable. Secret values are never put into error messages.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from ubc.schemas import SourceConfig, SourceKind, ordered_enabled_sources

# Registry name of the LLM wrapper. A source with this adapter needs LLM_API_KEY.
LLM_ADAPTER_NAME = "llm_extract"

_PROXY_KEYS = ("PROXY_HOST", "PROXY_LOGIN", "PROXY_PASSWORD", "PROXY_COUNTRY")


class SourceUnavailable(Exception):
    """This source cannot be called. The message does not contain secret values."""


def _clean(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    text = value.strip()
    return text or None


@dataclass(frozen=True)
class SecretConfig:
    """Proxy login and the LLM key, read from the environment or a mapping in tests."""

    proxy_host: str | None = None
    proxy_login: str | None = None
    proxy_password: str | None = field(default=None, repr=False)
    proxy_country: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SecretConfig:
        source = os.environ if env is None else env
        host, login, password, country = (_clean(source, key) for key in _PROXY_KEYS)
        return cls(
            proxy_host=host,
            proxy_login=login,
            proxy_password=password,
            proxy_country=country,
            llm_api_key=_clean(source, "LLM_API_KEY"),
        )

    @property
    def proxy_ready(self) -> bool:
        return None not in (
            self.proxy_host,
            self.proxy_login,
            self.proxy_password,
            self.proxy_country,
        )

    @property
    def llm_ready(self) -> bool:
        return self.llm_api_key is not None


def eligible_sources(sources: list[SourceConfig], secrets: SecretConfig) -> list[SourceConfig]:
    """Enabled sources the selection policy may call, highest priority first.

    A ``proxy_direct`` source without proxy secrets, and an ``llm_extract``
    source without ``LLM_API_KEY``, are omitted. The policy then continues
    with the next source. Disabled sources are already gone.
    """

    return [source for source in ordered_enabled_sources(sources) if _available(source, secrets)]


def _available(source: SourceConfig, secrets: SecretConfig) -> bool:
    if source.kind is SourceKind.proxy_direct and not secrets.proxy_ready:
        return False
    if source.adapter == LLM_ADAPTER_NAME and not secrets.llm_ready:
        return False
    return True
