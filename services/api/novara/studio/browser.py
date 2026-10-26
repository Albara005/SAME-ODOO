"""Browser checks: a version's pages loaded in headless Chromium on a desktop and a phone screen.

This is the one check that runs the project's own JavaScript, so it runs in a separate Node.js process (with a
timeout and an almost empty environment) and every request the page makes is answered from the version's files:
nothing reaches the network or the server's own address. See ``browser_check.cjs``.

Off by default in production: turn it on with ``NOVARA_BROWSER_CHECKS=on`` where Chromium's sandbox works (the API
not running as root), or on a separate machine.
"""

from __future__ import annotations

import base64
import json
import os
import posixpath
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from novara.studio.checks import PASS, WARN, CheckResult

SCRIPT = Path(__file__).with_name("browser_check.cjs")
MAX_PAGES = 10
TIMEOUT_SECONDS = 120
_PASS_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "PLAYWRIGHT_BROWSERS_PATH", "XDG_CACHE_HOME")


@dataclass
class BrowserReport:
    results: list[CheckResult] = field(default_factory=list)
    screenshots: dict[str, bytes] = field(default_factory=dict)  # desktop | mobile -> PNG of index.html


class BrowserChecker:
    def __init__(self, settings, script: Path = SCRIPT):
        self.enabled = settings.browser_checks == "on"
        self.sandbox = settings.browser_sandbox
        self.node_path = settings.browser_node_path
        self.script = script

    def _env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
        if self.node_path:
            env["NODE_PATH"] = self.node_path
        return env

    def run(self, files: dict[str, bytes]) -> BrowserReport:
        if not self.enabled:
            return BrowserReport()
        pages = sorted((p for p in files if posixpath.splitext(p)[1].lower() == ".html"),
                       key=lambda p: (p != "index.html", p.count("/"), p))
        if not pages:
            return BrowserReport()
        node = shutil.which("node")
        if node is None:
            return BrowserReport([CheckResult("browser", WARN, "Node.js not installed on the server; pages not loaded in a browser")])
        payload = json.dumps({"files": {p: base64.b64encode(b).decode() for p, b in files.items()},
                              "pages": pages[:MAX_PAGES], "screenshot": "index.html", "sandbox": self.sandbox})
        with tempfile.TemporaryDirectory(prefix="novara-browser-") as tmp:
            try:
                proc = subprocess.run([node, str(self.script)], input=payload, capture_output=True, text=True,
                                      timeout=TIMEOUT_SECONDS, cwd=tmp, env=self._env() | {"TMPDIR": tmp})
            except subprocess.TimeoutExpired:
                return BrowserReport([CheckResult("browser", WARN, f"browser check took longer than {TIMEOUT_SECONDS} s; not finished")])
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out = {}
        if "results" not in out:
            reason = out.get("error") or next(reversed(proc.stderr.strip().splitlines()), "no output")
            return BrowserReport([CheckResult("browser", WARN, f"browser check couldn't run: {reason[:240]}")])
        results = [CheckResult(r["check"], r["status"], r["message"], r.get("path", "")) for r in out["results"]]
        if len(pages) > MAX_PAGES:
            results.append(CheckResult("browser", WARN, f"only the first {MAX_PAGES} of {len(pages)} pages were loaded"))
        if not results:
            sandbox = "" if out.get("sandboxed") else " (without Chromium's sandbox)"
            results.append(CheckResult("browser", PASS, f"{len(pages)} page(s) load without errors on a desktop "
                                                        f"and a phone screen{sandbox}"))
        shots = {k: base64.b64decode(v) for k, v in (out.get("screenshots") or {}).items() if k in ("desktop", "mobile")}
        return BrowserReport(results, shots)


_current: BrowserChecker | None = None


def configure(checker: BrowserChecker | None) -> None:
    global _current
    _current = checker


def current() -> BrowserChecker | None:
    return _current
