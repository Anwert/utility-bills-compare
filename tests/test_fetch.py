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


def test_pdf_to_text_sends_bytes_to_pdftotext() -> None:
    def opener(request: object, timeout: float) -> bytes:
        return b"%PDF-fake"

    def command(argv: list[str], *, stdin: bytes | None = None) -> CommandResult:
        assert stdin == b"%PDF-fake"
        assert argv[:2] == ["pdftotext", "-layout"]
        return CommandResult(0, b"price 9.52\n", b"")

    text = HttpFetcher(opener=opener, command=command).pdf_to_text("https://example.test/a.pdf")

    assert text == "price 9.52\n"
