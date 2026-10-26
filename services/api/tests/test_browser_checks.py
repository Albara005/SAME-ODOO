"""Browser checks on App Studio versions: every page loaded in headless Chromium on a desktop and a phone screen,
with nothing reaching the network. Errors a visitor would hit block production, and each version keeps
screenshots of its home page."""

from __future__ import annotations

import http.server
import os
import shutil
import struct
import subprocess
import threading
from dataclasses import replace

import pytest

from novara.config import load_settings
from novara.studio import browser
from novara.studio.browser import BrowserChecker
from tests.test_studio import DEV, put_file, studio, url, version  # noqa: F401 (studio is a fixture)


def _playwright_installed() -> bool:
    node = shutil.which("node")
    if node is None:
        return False
    checker = BrowserChecker(load_settings())
    return subprocess.run([node, "-e", "require.resolve('playwright')"], env=checker._env(),
                          capture_output=True).returncode == 0


needs_browser = pytest.mark.skipif(not _playwright_installed(), reason="Playwright (Node.js) isn't installed")


@pytest.fixture
def checks_on(app):
    # Tests may run as root, where Chromium refuses its sandbox (production keeps it on by default).
    settings = replace(app.state.settings, browser_checks="on", browser_sandbox=False,
                       browser_node_path=load_settings().browser_node_path)
    browser.configure(BrowserChecker(settings))
    yield
    browser.configure(BrowserChecker(app.state.settings))


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def problems(v: dict) -> list[tuple[str, str, str]]:
    return [(c["check"], c["status"], c["message"]) for c in v["checks"] if c["check"].startswith("browser") and c["status"] != "pass"]


@needs_browser
def test_a_working_site_passes_and_keeps_screenshots(api, studio, checks_on):
    v = version(api, studio, "first")
    assert v["checks_passed"] and problems(v) == []
    assert any(c["check"] == "browser" and "desktop and a phone screen" in c["message"] for c in v["checks"])
    assert sorted(v["screenshots"]) == ["desktop", "mobile"]
    shots = {k: api.req("GET", DEV, url(studio, f"/versions/1/screenshots/{k}.png")) for k in ("desktop", "mobile")}
    assert {k: (r.status_code, r.headers["content-type"]) for k, r in shots.items()} == {
        "desktop": (200, "image/png"), "mobile": (200, "image/png")}
    assert png_size(shots["desktop"].content)[0] == 1280 and png_size(shots["mobile"].content)[0] == 390
    assert api.req("GET", DEV, url(studio, "/versions/1/screenshots/tablet.png")).status_code == 404


@needs_browser
def test_what_a_visitor_would_hit_blocks_production(api, studio, checks_on):
    put_file(api, studio, "app.js", "fetch('data/prices.json');\ndocument.getElementById('missing').textContent = 'x';\n")
    put_file(api, studio, "empty.html", '<!doctype html><html lang="en"><head><meta name="viewport" '
                                        'content="width=device-width"><title>e</title></head><body></body></html>')
    put_file(api, studio, "wide.html", '<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width">'
                                       '<title>w</title></head><body><div style="width:900px">Wide</div></body></html>')
    v = version(api, studio, "broken")
    found = problems(v)
    assert not v["checks_passed"]
    assert ("browser_errors", "fail", "Cannot set properties of null (setting 'textContent') (desktop, mobile)") in found
    assert ("browser_requests", "fail", "requested a file that doesn't exist: data/prices.json") in found
    assert ("browser_render", "fail", "the page is blank (desktop, mobile)") in found
    assert ("browser_layout", "warn", "wider than a phone screen by 518px: visitors must scroll sideways (mobile)") in found
    prod = api.req("POST", DEV, url(studio, "/deployments"), json={"version": v["number"], "environment": "production"})
    assert prod.status_code == 409

    # The builder sees the same problems while it works.
    working = api.req("POST", DEV, url(studio, "/checks")).json()
    assert any(c["check"] == "browser_errors" for c in working)


@needs_browser
def test_nothing_the_page_does_reaches_the_network(api, studio, checks_on):
    hits: list[str] = []

    class Listener(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Listener)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    here = f"127.0.0.1:{server.server_address[1]}"
    try:
        put_file(api, studio, "app.js", f"""
fetch('http://{here}/fetch').catch(() => {{}});
fetch('http://localhost:{server.server_address[1]}/by-name').catch(() => {{}});
new Image().src = 'http://{here}/image';
navigator.sendBeacon('http://{here}/beacon', 'x');
try {{ new WebSocket('ws://{here}/socket'); }} catch (e) {{}}
const frame = document.createElement('iframe'); frame.src = 'http://{here}/frame'; document.body.append(frame);
const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = 'http://{here}/style'; document.head.append(link);
new Worker(URL.createObjectURL(new Blob(["fetch('http://{here}/worker').catch(() => {{}})"])));
document.getElementById('output').textContent = 'ok';
""")
        v = version(api, studio, "phones home")
        server_hits = list(hits)
    finally:
        server.shutdown()
    assert server_hits == []
    assert ("browser_requests", "warn", f"loads from another site, not checked: {here}") in problems(v)
    assert v["checks_passed"]                     # only warnings: the site itself works


@needs_browser
def test_an_error_after_live_data_is_only_a_warning(api, studio, checks_on):
    put_file(api, studio, "app.js", "fetch('/api/v1/public/sites/nokhba-shop/catalog').then((r) => r.json())"
                                    ".then((data) => { document.getElementById('output').textContent = data.items.length; });\n")
    v = version(api, studio, "catalog")
    (error,) = [p for p in problems(v) if p[0] == "browser_errors"]
    assert error[1] == "warn" and "live data isn't loaded during checks" in error[2]
    assert v["checks_passed"]


def test_without_a_browser_the_check_is_skipped_with_a_note(app, tmp_path, monkeypatch):
    files = {"index.html": b"<!doctype html><html lang='en'><title>x</title><body>Hi</body></html>"}
    off = BrowserChecker(app.state.settings)
    assert not off.enabled and off.run(files).results == []

    settings = replace(app.state.settings, browser_checks="on", browser_sandbox=False)
    if shutil.which("node"):
        canned = tmp_path / "canned.cjs"
        canned.write_text('process.stdout.write(JSON.stringify({results: [], screenshots: {desktop: "iVBORw0K"}, sandboxed: true}))')
        report = BrowserChecker(settings, script=canned).run(files)
        assert [(r.check, r.status) for r in report.results] == [("browser", "pass")]
        assert "sandbox" not in report.results[0].message and report.screenshots == {"desktop": b"\x89PNG\r\n"}
        crashed = tmp_path / "crashed.cjs"
        crashed.write_text("process.stderr.write('Segmentation fault'); process.exit(139)")
        (note,) = BrowserChecker(settings, script=crashed).run(files).results
        assert (note.status, note.message) == ("warn", "browser check couldn't run: Segmentation fault")
        empty = tmp_path / "modules"
        empty.mkdir()
        (note,) = BrowserChecker(replace(settings, browser_node_path=str(empty))).run(files).results
        assert note.status == "warn" and "Playwright isn't installed on the server" in note.message

    monkeypatch.setattr(shutil, "which", lambda name: None)
    (note,) = BrowserChecker(settings).run(files).results
    assert (note.status, note.message) == ("warn", "Node.js not installed on the server; pages not loaded in a browser")
    assert BrowserChecker(settings).run({"data.json": b"{}"}).results == []          # no pages, nothing to load


@needs_browser
@pytest.mark.skipif(not hasattr(os, "getuid") or os.getuid() != 0, reason="only as root does Chromium refuse its sandbox")
def test_a_sandbox_that_cant_start_is_reported(app):
    settings = replace(app.state.settings, browser_checks="on", browser_sandbox=True,
                       browser_node_path=load_settings().browser_node_path)
    (note,) = BrowserChecker(settings).run({"index.html": b"<html lang='en'><body>Hi</body></html>"}).results
    assert note.status == "warn" and "Chromium couldn't start with its sandbox" in note.message


def test_production_leaves_browser_checks_off_and_sandboxed(monkeypatch):
    monkeypatch.setenv("NOVARA_ENV", "production")
    monkeypatch.setenv("NOVARA_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("NOVARA_ENCRYPTION_KEY", "y" * 40)
    monkeypatch.delenv("NOVARA_BROWSER_CHECKS", raising=False)
    monkeypatch.delenv("NOVARA_BROWSER_SANDBOX", raising=False)
    settings = load_settings()
    assert (settings.browser_checks, settings.browser_sandbox) == ("off", True)
    monkeypatch.setenv("NOVARA_BROWSER_CHECKS", "maybe")
    with pytest.raises(RuntimeError, match="NOVARA_BROWSER_CHECKS"):
        load_settings()
