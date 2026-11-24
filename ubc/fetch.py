"""How an adapter obtains HTML, JSON, or PDF text.

Two implementations share one interface:

- ``FixtureFetcher`` reads files saved in the repo. Tests use only this one.
- ``HttpFetcher`` does the live request: HTTP, with curl only when the
  failure is a TLS handshake or a connection refusal (not an HTTP status
  and not an arbitrary exception), headless Chrome for pages that are empty
  without JS, and pdftotext for PDF bytes. ``post_bytes`` is the same HTTP
  path with a request body (the Armenian central-bank SOAP call).

Neither implementation disables TLS verification or tries to pass an anti-bot
challenge. A blocked response is a failed source; the selection policy moves on.
"""

from __future__ import annotations

import json
import shutil
import socket
import ssl
import subprocess
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

USER_AGENT = "utility-bills-compare/0.1 (+https://github.com/Anwert/utility-bills-compare)"


class FetchError(Exception):
    """The content could not be obtained. The message names the URL and the reason."""

    def __init__(self, url: str, message: str) -> None:
        self.url = url
        super().__init__(f"{url}: {message}" if url else message)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    def __call__(self, argv: list[str], *, stdin: bytes | None = None) -> CommandResult: ...


class ContentFetcher(Protocol):
    """What every adapter receives. Implementations must not be mixed inside one call."""

    def fetch_bytes(self, url: str) -> bytes: ...

    def fetch_text(self, url: str) -> str: ...

    def post_bytes(self, url: str, body: bytes, headers: dict[str, str] | None = None) -> bytes: ...

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str: ...

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str: ...


def _decode(body: bytes) -> str:
    return body.decode("utf-8", "replace")


class FixtureFetcher:
    """Saved HTML, JSON, PDF, or text. Never opens a socket.

    Resolution order for a URL:

    1. ``fixture:<relative path>`` inside ``root``.
    2. ``root/manifest.json`` (or the mapping passed in), URL to relative path.

    ``render_html`` returns the saved file. For a JS page, save the DOM after
    rendering. ``pdf_to_text`` prefers a sibling ``.txt`` of the same stem so
    tests do not need poppler; otherwise it runs pdftotext on the local file.
    ``post_bytes`` resolves the URL and ignores the body: put the month in the
    URL so two periods do not share one fixture.
    """

    def __init__(self, root: Path, manifest: dict[str, str] | None = None) -> None:
        self.root = root.resolve()
        if manifest is None:
            manifest_path = self.root / "manifest.json"
            if manifest_path.is_file():
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise FetchError("", "fixture manifest must be a JSON object")
                manifest = {str(key): str(value) for key, value in loaded.items()}
            else:
                manifest = {}
        self.manifest = manifest

    def resolve(self, url: str) -> Path:
        if url.startswith("fixture:"):
            relative = url.removeprefix("fixture:")
        elif url in self.manifest:
            relative = self.manifest[url]
        else:
            raise FetchError(url, "no fixture for this URL (network is not used)")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise FetchError(url, "fixture path escapes the fixture directory")
        if not path.is_file():
            raise FetchError(url, "fixture file is missing")
        return path

    def fetch_bytes(self, url: str) -> bytes:
        return self.resolve(url).read_bytes()

    def fetch_text(self, url: str) -> str:
        return self.resolve(url).read_text(encoding="utf-8")

    def post_bytes(self, url: str, body: bytes, headers: dict[str, str] | None = None) -> bytes:
        del body, headers
        return self.fetch_bytes(url)

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        del budget_ms
        return self.fetch_text(url)

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        path = self.resolve(url)
        if path.suffix.lower() == ".txt":
            return path.read_text(encoding="utf-8")
        sidecar = path.with_suffix(".txt")
        if sidecar.is_file():
            return sidecar.read_text(encoding="utf-8")
        if path.suffix.lower() != ".pdf":
            raise FetchError(url, "fixture is not a PDF or a .txt sidecar")
        return _pdftotext(path.read_bytes(), layout=layout, url=url, command=_default_command)


class HttpFetcher:
    """Live fetches. Construct this only from the pipeline, never from a test.

    ``opener`` and ``command`` exist so the fallback behaviour can be tested
    without a socket: pass fakes. The defaults call urllib and subprocess.
    """

    def __init__(
        self,
        *,
        timeout: float = 40.0,
        user_agent: str = USER_AGENT,
        opener: Opener | None = None,
        command: CommandRunner | None = None,
        curl_bin: str = "curl",
        chrome_bin: str | None = None,
        pdftotext_bin: str = "pdftotext",
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.opener = opener or _default_opener
        self.command = command or _default_command
        self.curl_bin = curl_bin
        self.chrome_bin = chrome_bin
        self.pdftotext_bin = pdftotext_bin

    def fetch_bytes(self, url: str) -> bytes:
        try:
            return self.opener(_request(url, self.user_agent), self.timeout)
        except Exception as exc:
            if not _connection_failure(exc):
                raise FetchError(url, f"HTTP failed ({exc})") from exc
            try:
                curl_result = _curl(self, url)
            except Exception as curl_exc:
                raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_exc})") from exc
            if isinstance(curl_result, bytes):
                return curl_result
            raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_result})") from exc

    def fetch_text(self, url: str) -> str:
        return _decode(self.fetch_bytes(url))

    def post_bytes(self, url: str, body: bytes, headers: dict[str, str] | None = None) -> bytes:
        try:
            return self.opener(_post_request(url, body, headers, self.user_agent), self.timeout)
        except Exception as exc:
            if not _connection_failure(exc):
                raise FetchError(url, f"HTTP failed ({exc})") from exc
            try:
                curl_result = _curl(self, url, body=body, headers=headers)
            except Exception as curl_exc:
                raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_exc})") from exc
            if isinstance(curl_result, bytes):
                return curl_result
            raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_result})") from exc

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        binary = _chrome_binary(self.chrome_bin, url)
        result = self.command(
            [
                binary,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                f"--virtual-time-budget={budget_ms}",
                f"--user-agent={self.user_agent}",
                "--dump-dom",
                url,
            ]
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise FetchError(url, f"headless Chrome failed ({detail})")
        return _decode(result.stdout)

    def pdf_to_text(self, url: str, *, layout: bool = True) -> str:
        body = self.fetch_bytes(url)
        return _pdftotext(
            body,
            layout=layout,
            url=url,
            command=self.command,
            binary=self.pdftotext_bin,
        )


class Opener(Protocol):
    def __call__(self, request: object, timeout: float) -> bytes: ...


def _request(url: str, user_agent: str) -> object:
    from urllib.request import Request

    return Request(url, headers=_headers(user_agent, None))


def _post_request(url: str, body: bytes, headers: dict[str, str] | None, user_agent: str) -> object:
    from urllib.request import Request

    return Request(url, data=body, headers=_headers(user_agent, headers), method="POST")


def _headers(user_agent: str, extra: dict[str, str] | None) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": "en,ru;q=0.8",
    }
    if extra:
        headers.update(extra)
    return headers


def _default_opener(request: object, timeout: float) -> bytes:
    from urllib.request import urlopen

    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _default_command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
    completed = subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        timeout=90,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _connection_failure(exc: BaseException) -> bool:
    """True when urllib never received an HTTP response.

    Curl then retries the same URL with certificate checks still on. HTTP
    status errors and programming errors are not retried.
    """

    if isinstance(exc, urllib.error.HTTPError):
        return False
    reason: BaseException = exc
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        reason = exc.reason
    return isinstance(
        reason, (ssl.SSLError, TimeoutError, socket.timeout, ConnectionError, socket.gaierror)
    )


def _curl(
    fetcher: HttpFetcher,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes | str:
    argv = [
        fetcher.curl_bin,
        "-fsSL",
        "--compressed",
        "-m",
        str(int(fetcher.timeout)),
        "-A",
        fetcher.user_agent,
    ]
    for key, value in (headers or {}).items():
        argv.extend(["-H", f"{key}: {value}"])
    if body is not None:
        argv.extend(["--data-binary", "@-"])
    argv.append(url)
    result = fetcher.command(argv, stdin=body)
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exit {result.returncode}"
        return detail
    return result.stdout


def _chrome_binary(explicit: str | None, url: str) -> str:
    if explicit:
        if Path(explicit).is_file() or shutil.which(explicit):
            return explicit
        raise FetchError(url, "headless Chrome is not installed")
    found = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if not found:
        raise FetchError(url, "headless Chrome is not installed")
    return found


def _pdftotext(
    body: bytes,
    *,
    layout: bool,
    url: str,
    command: CommandRunner,
    binary: str = "pdftotext",
) -> str:
    argv = [binary]
    if layout:
        argv.append("-layout")
    argv.extend(["-", "-"])
    result = command(argv, stdin=body)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise FetchError(url, f"pdftotext failed ({detail})")
    return _decode(result.stdout)
