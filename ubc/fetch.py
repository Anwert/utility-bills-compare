"""How an adapter obtains HTML, JSON, or PDF text.

Two implementations share one interface:

- ``FixtureFetcher`` reads files saved in the repo. Tests use only this one.
- ``HttpFetcher`` does the live request: HTTP, with curl only when the
  failure is a TLS handshake or a connection refusal (not an HTTP status
  and not an arbitrary exception), headless Chrome for pages that are empty
  without JS, and pdftotext for PDF bytes. ``post_text`` is the same
  transport for a request body (the Armenian central-bank SOAP call).

Neither implementation disables TLS verification or tries to pass an anti-bot
challenge. A blocked response is a failed source; the selection policy moves on.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_CHROME_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)
_WINDOWS_PROGRAM_DIRS = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
USER_AGENT = "utility-bills-compare/0.1 (+https://github.com/Anwert/utility-bills-compare)"


class FetchError(Exception):
    """The content could not be obtained. The message names the URL and the reason.

    ``status`` is the HTTP code when the server answered with an error.
    """

    def __init__(self, url: str, message: str, *, status: int | None = None) -> None:
        self.url = url
        self.status = status
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

    def post_text(
        self,
        url: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str: ...

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

    def post_text(
        self,
        url: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Saved response for a POST. The URL, including its query, selects the file."""

        del body, headers
        return self.fetch_text(url)

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
                status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                raise FetchError(url, f"HTTP failed ({exc})", status=status) from exc
            try:
                curl_result = _curl(self, url)
            except Exception as curl_exc:
                raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_exc})") from exc
            if isinstance(curl_result, bytes):
                return curl_result
            raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_result})") from exc

    def fetch_text(self, url: str) -> str:
        return _decode(self.fetch_bytes(url))

    def post_text(
        self,
        url: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        payload = body.encode("utf-8")
        extra = dict(headers or {})
        try:
            return _decode(
                self.opener(_post_request(url, self.user_agent, payload, extra), self.timeout)
            )
        except Exception as exc:
            if not _connection_failure(exc):
                raise FetchError(url, f"HTTP failed ({exc})") from exc
            try:
                curl_result = _curl_post(self, url, payload, extra)
            except Exception as curl_exc:
                raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_exc})") from exc
            if isinstance(curl_result, bytes):
                return _decode(curl_result)
            raise FetchError(url, f"HTTP failed ({exc}); curl failed ({curl_result})") from exc

    def render_html(self, url: str, *, budget_ms: int = 15000) -> str:
        binary = _chrome_binary(self.chrome_bin, url)
        # A throwaway profile: on Windows a Chrome already open on the default
        # profile would take the URL over and print no DOM.
        with tempfile.TemporaryDirectory(
            prefix="ubc-chrome-", ignore_cleanup_errors=True
        ) as profile:
            result = _run(
                self.command,
                [
                    binary,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    f"--user-data-dir={profile}",
                    f"--virtual-time-budget={budget_ms}",
                    f"--user-agent={self.user_agent}",
                    "--dump-dom",
                    url,
                ],
                url=url,
                program="headless Chrome",
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


def _post_request(url: str, user_agent: str, body: bytes, headers: dict[str, str]) -> object:
    from urllib.request import Request

    merged = {
        "User-Agent": user_agent,
        "Accept-Language": "en,ru;q=0.8",
    }
    merged.update(headers)
    return Request(url, data=body, headers=merged, method="POST")


def _request(url: str, user_agent: str) -> object:
    from urllib.request import Request

    return Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept-Language": "en,ru;q=0.8",
        },
    )


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


def _run(
    command: CommandRunner,
    argv: list[str],
    *,
    url: str,
    program: str,
    stdin: bytes | None = None,
) -> CommandResult:
    """Run ``command``. A program that is missing or hangs is a failed source."""

    try:
        return command(argv, stdin=stdin)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FetchError(url, f"{program} failed ({exc})") from exc


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


def _curl_post(fetcher: HttpFetcher, url: str, body: bytes, headers: dict[str, str]) -> bytes | str:
    argv = [
        fetcher.curl_bin,
        "-fsSL",
        "--compressed",
        "-m",
        str(int(fetcher.timeout)),
        "-A",
        fetcher.user_agent,
        "--data-binary",
        "@-",
    ]
    argv.extend(header for key, value in headers.items() for header in ("-H", f"{key}: {value}"))
    argv.append(url)
    result = fetcher.command(argv, stdin=body)
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exit {result.returncode}"
        return detail
    return result.stdout


def _curl(fetcher: HttpFetcher, url: str) -> bytes | str:
    result = fetcher.command(
        [
            fetcher.curl_bin,
            "-fsSL",
            "--compressed",
            "-m",
            str(int(fetcher.timeout)),
            "-A",
            fetcher.user_agent,
            url,
        ]
    )
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exit {result.returncode}"
        return detail
    return result.stdout


def _chrome_binary(explicit: str | None, url: str) -> str:
    """The Chrome to launch: the caller's, then ``CHROME_BIN``, then an installed one."""

    chosen = explicit or os.environ.get("CHROME_BIN")
    if chosen:
        if Path(chosen).is_file() or shutil.which(chosen):
            return chosen
        raise FetchError(url, "headless Chrome is not installed")
    for name in _CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for variable in _WINDOWS_PROGRAM_DIRS:
        root = os.environ.get(variable)
        if root:
            installed = Path(root, "Google", "Chrome", "Application", "chrome.exe")
            if installed.is_file():
                return str(installed)
    raise FetchError(url, "headless Chrome is not installed")


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
    # A file, not stdin: xpdf's pdftotext, usual on Windows, reads only files.
    # UTF-8 and unix line ends are poppler's defaults but not xpdf's.
    with tempfile.TemporaryDirectory(prefix="ubc-pdf-", ignore_cleanup_errors=True) as folder:
        pdf = Path(folder, "document.pdf")
        pdf.write_bytes(body)
        argv.extend(["-enc", "UTF-8", "-eol", "unix", str(pdf), "-"])
        result = _run(command, argv, url=url, program="pdftotext")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise FetchError(url, f"pdftotext failed ({detail})")
    return _decode(result.stdout)
