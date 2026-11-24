"""Content layer: fixtures stay local, and the HTTP fallback is explicit."""

from __future__ import annotations

import ssl
import subprocess
import urllib.error
from pathlib import Path

import pytest

from ubc.fetch import CommandResult, FetchError, FixtureFetcher, HttpFetcher


def test_fixture_fetcher_reads_a_mapped_file_and_a_relative_name(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("<p>tariff</p>", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        '{"https://example.test/page": "page.html"}\n',
        encoding="utf-8",
    )
    fetcher = FixtureFetcher(tmp_path)

    assert fetcher.fetch_text("https://example.test/page") == "<p>tariff</p>"
    assert fetcher.render_html("fixture:page.html") == "<p>tariff</p>"
    assert fetcher.fetch_bytes("fixture:page.html") == b"<p>tariff</p>"


def test_missing_fixture_does_not_fall_through_to_the_network(tmp_path: Path) -> None:
    fetcher = FixtureFetcher(tmp_path, {})

    with pytest.raises(FetchError, match="network is not used"):
        fetcher.fetch_text("https://example.test/missing")


def test_fixture_path_cannot_escape_the_directory(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    fetcher = FixtureFetcher(root, {"https://example.test/x": "../secret.txt"})

    with pytest.raises(FetchError, match="escapes"):
        fetcher.fetch_text("https://example.test/x")


def test_pdf_text_prefers_the_sidecar_file(tmp_path: Path) -> None:
    (tmp_path / "tariff.pdf").write_bytes(b"%PDF-fake")
    (tmp_path / "tariff.txt").write_text("tariff 1.5\n", encoding="utf-8")
    fetcher = FixtureFetcher(tmp_path, {"https://example.test/tariff.pdf": "tariff.pdf"})

    assert fetcher.pdf_to_text("https://example.test/tariff.pdf") == "tariff 1.5\n"


def test_http_success_does_not_call_curl() -> None:
    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        raise AssertionError(argv)

    body = HttpFetcher(opener=lambda request, timeout: b"ok", command=command).fetch_bytes(
        "https://example.test/ok"
    )

    assert body == b"ok"


def test_post_text_sends_the_body_and_keeps_tls_verification() -> None:
    seen: dict[str, object] = {}

    def opener(request: object, timeout: float) -> bytes:
        del timeout
        seen["data"] = getattr(request, "data", None)
        seen["headers"] = dict(request.header_items())  # type: ignore[attr-defined]
        return b"<ok/>"

    def refuse_command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        raise AssertionError(argv)

    text = HttpFetcher(opener=opener, command=refuse_command).post_text(
        "https://example.test/soap",
        "<ISOCodes>EUR</ISOCodes>",
        headers={"SOAPAction": "http://www.cba.am/ExchangeRatesByDateRangeByISO"},
    )

    assert text == "<ok/>"
    assert seen["data"] == b"<ISOCodes>EUR</ISOCodes>"
    headers = {key.lower(): value for key, value in seen["headers"].items()}  # type: ignore[attr-defined]
    assert headers["soapaction"] == "http://www.cba.am/ExchangeRatesByDateRangeByISO"

    def refuse(request: object, timeout: float) -> bytes:
        del request, timeout
        raise ssl.SSLError("tls handshake rejected")

    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        seen["argv"] = argv
        seen["stdin"] = stdin
        return CommandResult(0, b"<ok/>", b"")

    fallback = HttpFetcher(opener=refuse, command=command).post_text(
        "https://example.test/soap",
        "<body/>",
        headers={"SOAPAction": "urn:example"},
    )

    assert fallback == "<ok/>"
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "curl"
    assert "-k" not in argv
    assert "--insecure" not in argv
    assert "--data-binary" in argv
    assert "SOAPAction: urn:example" in argv
    assert seen["stdin"] == b"<body/>"


def test_curl_fallback_keeps_tls_verification() -> None:
    seen: dict[str, list[str]] = {}

    def opener(request: object, timeout: float) -> bytes:
        raise ssl.SSLError("tls handshake rejected")

    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        seen["argv"] = argv
        return CommandResult(0, b"<html></html>", b"")

    body = HttpFetcher(opener=opener, command=command).fetch_bytes("https://example.test/page")

    assert body == b"<html></html>"
    assert seen["argv"][0] == "curl"
    assert "-k" not in seen["argv"]
    assert "--insecure" not in seen["argv"]
    assert "https://example.test/page" in seen["argv"]


def test_curl_failure_is_a_fetch_error() -> None:
    def opener(request: object, timeout: float) -> bytes:
        raise ConnectionRefusedError("refused")

    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        return CommandResult(22, b"", b"http 404")

    with pytest.raises(FetchError, match="curl failed"):
        HttpFetcher(opener=opener, command=command).fetch_bytes("https://example.test/missing")


def test_http_status_and_other_errors_do_not_call_curl() -> None:
    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        raise AssertionError(argv)

    def status(request: object, timeout: float) -> bytes:
        raise urllib.error.HTTPError("https://example.test/missing", 404, "missing", {}, None)

    def bug(request: object, timeout: float) -> bytes:
        raise ValueError("parser bug")

    with pytest.raises(FetchError, match="HTTP failed"):
        HttpFetcher(opener=status, command=command).fetch_bytes("https://example.test/missing")
    with pytest.raises(FetchError, match="HTTP failed"):
        HttpFetcher(opener=bug, command=command).fetch_bytes("https://example.test/missing")


def test_default_curl_path_is_blocked_in_tests() -> None:
    def opener(request: object, timeout: float) -> bytes:
        raise ssl.SSLError("handshake failed")

    with pytest.raises(FetchError, match="network is disabled"):
        HttpFetcher(opener=opener).fetch_bytes("https://example.test/x")


def test_conftest_blocks_curl_and_chrome_subprocesses() -> None:
    with pytest.raises(RuntimeError, match="network is disabled"):
        subprocess.run(["curl", "-I", "https://example.test"], check=False)
    with pytest.raises(RuntimeError, match="network is disabled"):
        subprocess.run(["/usr/bin/google-chrome", "--version"], check=False)


def test_render_html_builds_a_headless_chrome_command(tmp_path: Path) -> None:
    binary = tmp_path / "chrome"
    binary.write_text("", encoding="utf-8")
    seen: dict[str, list[str]] = {}

    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        seen["argv"] = argv
        return CommandResult(0, b"<html>ok</html>", b"")

    text = HttpFetcher(command=command, chrome_bin=str(binary)).render_html(
        "https://example.test/app"
    )

    assert text == "<html>ok</html>"
    assert seen["argv"][0] == str(binary)
    assert "--headless=new" in seen["argv"]
    assert "--dump-dom" in seen["argv"]
    assert "--virtual-time-budget=15000" in seen["argv"]
    assert "https://example.test/app" in seen["argv"]
    assert "-k" not in seen["argv"]


def test_missing_chrome_fails_before_launch() -> None:
    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        raise AssertionError(argv)

    with pytest.raises(FetchError, match="Chrome"):
        HttpFetcher(command=command, chrome_bin="/no/such/chrome").render_html(
            "https://example.test/app"
        )


def _render_with(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> list[str]:
    """Render with only ``env`` describing where Chrome may be. Returns the argv."""

    for name in ("CHROME_BIN", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    seen: list[str] = []

    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        seen.extend(argv)
        return CommandResult(0, "<html>Ջուր</html>".encode(), b"")

    assert HttpFetcher(command=command).render_html("https://example.test/app") == (
        "<html>Ջուր</html>"
    )
    return seen


@pytest.mark.parametrize("root", ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"])
def test_chrome_is_found_in_its_windows_install_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: str
) -> None:
    binary = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")

    assert _render_with(monkeypatch, {root: str(tmp_path)})[0] == str(binary)


def test_chrome_bin_overrides_the_installed_chrome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installed = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    chosen = tmp_path / "my-chrome"
    chosen.write_text("", encoding="utf-8")

    argv = _render_with(monkeypatch, {"CHROME_BIN": str(chosen), "PROGRAMFILES": str(tmp_path)})

    assert argv[0] == str(chosen)


def test_chrome_runs_in_a_throwaway_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chosen = tmp_path / "my-chrome"
    chosen.write_text("", encoding="utf-8")

    argv = _render_with(monkeypatch, {"CHROME_BIN": str(chosen)})

    (profile,) = [arg for arg in argv if arg.startswith("--user-data-dir=")]
    assert not Path(profile.removeprefix("--user-data-dir=")).exists()


def test_a_program_that_cannot_start_is_a_fetch_error(tmp_path: Path) -> None:
    binary = tmp_path / "chrome"
    binary.write_text("", encoding="utf-8")

    def missing(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        raise FileNotFoundError(argv[0])

    def slow(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        raise subprocess.TimeoutExpired(argv, 90)

    for command in (missing, slow):
        fetcher = HttpFetcher(
            opener=lambda request, timeout: b"%PDF-fake",
            command=command,
            chrome_bin=str(binary),
        )
        with pytest.raises(FetchError, match="Chrome"):
            fetcher.render_html("https://example.test/app")
        with pytest.raises(FetchError, match="pdftotext"):
            fetcher.pdf_to_text("https://example.test/a.pdf")


def test_pdf_to_text_sends_bytes_to_pdftotext() -> None:
    seen: list[str] = []

    def opener(request: object, timeout: float) -> bytes:
        return b"%PDF-fake"

    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        assert argv[:2] == ["pdftotext", "-layout"]
        # xpdf's pdftotext, usual on Windows, does not read a PDF from stdin.
        assert Path(argv[-2]).read_bytes() == b"%PDF-fake"
        assert argv[-1] == "-"
        seen.append(argv[-2])
        return CommandResult(0, "price 9.52 Ջուր\n".encode(), b"")

    text = HttpFetcher(opener=opener, command=command).pdf_to_text("https://example.test/a.pdf")

    assert text == "price 9.52 Ջուր\n"
    assert not Path(seen[0]).exists()
