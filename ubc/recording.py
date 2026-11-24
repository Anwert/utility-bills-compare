"""Keep what a source answered, so the repository has the raw response.

``RecordingFetcher`` wraps any ``ContentFetcher``. The wrapped fetcher may be a
proxy: the recording sees the URL and the content, never the endpoint. Values
passed as ``hide`` are blanked anyway, in case a page or an error echoes one.

One source becomes one ``.tar.xz``. The pages of one listing site repeat the
same markup, so a solid archive is several times smaller than a gzip per page.
The bytes depend only on the answers: an unchanged source is an unchanged file.
"""

from __future__ import annotations

import io
import json
import lzma
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass

from ubc.fetch import ContentFetcher

_HIDDEN = "***"
# Shorter values would blank ordinary text ("pt", "1").
_MIN_HIDDEN = 4


class Redactor:
    """Blank secret values in text and bytes before they are stored or sent."""

    def __init__(self, values: Iterable[str | None] = ()) -> None:
        kept = {value for value in values if value is not None and len(value) >= _MIN_HIDDEN}
        self._values = sorted(kept, key=len, reverse=True)

    def text(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, _HIDDEN)
        return text

    def data(self, data: bytes) -> bytes:
        for value in self._values:
            data = data.replace(value.encode("utf-8"), _HIDDEN.encode("ascii"))
        return data


@dataclass(frozen=True, slots=True)
class _Answer:
    method: str
    url: str
    body: bytes | None
    error: str = ""
    request: str = ""


class RecordingFetcher:
    """``ContentFetcher`` that remembers every answer of the fetcher it wraps."""

    def __init__(self, inner: ContentFetcher, redactor: Redactor | None = None) -> None:
        self._inner = inner
        self._redactor = redactor or Redactor()
        self._answers: list[_Answer] = []

    def fetch_bytes(self, url: str) -> bytes:
        return self._keep("fetch_bytes", url, lambda: self._inner.fetch_bytes(url))

    def fetch_text(self, url: str) -> str:
        return self._keep("fetch_text", url, lambda: self._inner.fetch_text(url))

    def post_text(
        self,
        url: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self._keep(
            "post_text",
            url,
            lambda: self._inner.post_text(url, body, headers=headers),
            request=body,
        )

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        return self._keep(
            "render_html", url, lambda: self._inner.render_html(url, budget_ms=budget_ms)
        )

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        return self._keep("pdf_to_text", url, lambda: self._inner.pdf_to_text(url, layout=layout))

    def archive(self) -> bytes | None:
        """Deterministic ``.tar.xz`` of every answer, or ``None`` if nothing was asked."""

        if not self._answers:
            return None
        index: list[dict[str, object]] = []
        members: list[tuple[str, bytes]] = []
        for number, answer in enumerate(self._answers, start=1):
            entry: dict[str, object] = {"method": answer.method, "url": answer.url}
            if answer.request:
                entry["request"] = answer.request
            if answer.body is None:
                entry["error"] = answer.error
            else:
                name = f"{number:03d}.{_extension(answer.body)}"
                entry["file"] = name
                members.append((name, answer.body))
            index.append(entry)
        listing = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
        members.insert(0, ("index.json", listing.encode("utf-8")))
        packed = io.BytesIO()
        with tarfile.open(fileobj=packed, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for name, body in members:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        return lzma.compress(packed.getvalue(), preset=6)

    def _keep[T: (str, bytes)](self, method: str, url: str, call, request: str = "") -> T:
        hidden_url = self._redactor.text(url)
        hidden_request = self._redactor.text(request)
        try:
            body = call()
        except Exception as exc:
            error = self._redactor.text(str(exc))
            self._answers.append(_Answer(method, hidden_url, None, error, hidden_request))
            raise
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self._answers.append(
            _Answer(method, hidden_url, self._redactor.data(raw), request=hidden_request)
        )
        return body


def _extension(body: bytes) -> str:
    head = body[:64].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"{", b"[")):
        return "json"
    if head.startswith(b"<?xml"):
        return "xml"
    if head.startswith(b"<"):
        return "html"
    return "txt"
