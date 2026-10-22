"""Checks that run on every version. A version with any failed check can't go to production.

None of these execute project code. JavaScript is only *parsed* (``node --check``), in
a separate process with a timeout and an empty environment.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

from novara.studio.templates import Template

PASS, WARN, FAIL = "pass", "warn", "fail"

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic API key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "API secret key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
]
_EXTERNAL = ("http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:", "#")
TEXT_EXTENSIONS = {".html", ".css", ".js", ".json", ".svg", ".txt", ".md"}


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: str
    message: str
    path: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class _HtmlScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html_attrs: dict[str, str | None] | None = None
        self.title = ""
        self.in_title = False
        self.viewport = False
        self.refs: list[tuple[str, str]] = []  # (tag, url)
        self.imgs_without_alt = 0
        self.external_scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.html_attrs = a
        elif tag == "title":
            self.in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "viewport":
            self.viewport = True
        elif tag == "img" and "alt" not in a:
            self.imgs_without_alt += 1
        for attr in ("href", "src"):
            if a.get(attr):
                self.refs.append((tag, a[attr].strip()))
        if tag == "script" and (a.get("src") or "").startswith(("http://", "https://", "//")):
            self.external_scripts.append(a["src"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def _ext(path: str) -> str:
    return posixpath.splitext(path)[1].lower()


def _resolve(base_file: str, url: str) -> str | None:
    """Project path a relative URL points to, or None when it's external or an anchor."""
    if not url or url.startswith(_EXTERNAL):
        return None
    url = url.split("#", 1)[0].split("?", 1)[0]
    if not url:
        return None
    joined = url.lstrip("/") if url.startswith("/") else posixpath.join(posixpath.dirname(base_file), url)
    if url.endswith("/") or joined in ("", "."):
        joined = posixpath.join(joined, "index.html")
    return posixpath.normpath(joined)


def _js_syntax(files: dict[str, bytes]) -> list[CheckResult]:
    scripts = {p: b for p, b in files.items() if _ext(p) == ".js"}
    if not scripts:
        return [CheckResult("javascript_syntax", PASS, "no JavaScript files")]
    node = shutil.which("node")
    if node is None:
        return [CheckResult("javascript_syntax", WARN, "Node.js not installed on the server; syntax not checked")]
    results = []
    with tempfile.TemporaryDirectory(prefix="novara-js-") as tmp:
        for path, body in sorted(scripts.items()):
            target = os.path.join(tmp, "check.js")
            with open(target, "wb") as f:
                f.write(body)
            try:
                proc = subprocess.run([node, "--check", target], capture_output=True, timeout=10, env={},
                                      cwd=tmp, text=True)
            except subprocess.TimeoutExpired:
                results.append(CheckResult("javascript_syntax", FAIL, "syntax check timed out", path))
                continue
            if proc.returncode != 0:
                lines = [l for l in proc.stderr.splitlines() if l.strip() and not l.startswith("Node.js")]
                detail = next((l for l in lines if "Error" in l), lines[-1] if lines else "syntax error")
                results.append(CheckResult("javascript_syntax", FAIL, detail.replace(target, path)[:300], path))
    return results or [CheckResult("javascript_syntax", PASS, f"{len(scripts)} file(s) parsed")]


def run_checks(template: Template, files: dict[str, bytes]) -> list[CheckResult]:
    out: list[CheckResult] = []
    out.append(CheckResult("entry_point", PASS if "index.html" in files else FAIL,
                           "index.html found" if "index.html" in files else "index.html is missing"))

    bad_types = [p for p in files if _ext(p) not in template.allowed_extensions]
    out += [CheckResult("file_types", FAIL, f"file type not allowed in this template: {_ext(p) or '(none)'}", p)
            for p in sorted(bad_types)] or [CheckResult("file_types", PASS, "all file types allowed")]

    for path, body in sorted(files.items()):
        if _ext(path) not in TEXT_EXTENSIONS:
            continue
        text = body.decode("utf-8", errors="replace")
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                out.append(CheckResult("secrets", FAIL, f"looks like an exposed {label}; the site is public - remove it", path))
    if not any(r.check == "secrets" for r in out):
        out.append(CheckResult("secrets", PASS, "no secrets found"))

    for path, body in sorted(files.items()):
        if _ext(path) != ".html":
            continue
        scan = _HtmlScan()
        scan.feed(body.decode("utf-8", errors="replace"))
        attrs = scan.html_attrs or {}
        if not attrs.get("lang"):
            out.append(CheckResult("html_structure", FAIL, "<html> needs a lang attribute", path))
        elif attrs["lang"].lower().startswith("ar") and (attrs.get("dir") or "").lower() != "rtl":
            out.append(CheckResult("html_structure", WARN, "Arabic page without dir=\"rtl\"", path))
        if not scan.viewport:
            out.append(CheckResult("html_structure", WARN, "missing <meta name=\"viewport\"> (mobile layout)", path))
        if not scan.title.strip():
            out.append(CheckResult("html_structure", WARN, "missing <title>", path))
        if scan.imgs_without_alt:
            out.append(CheckResult("accessibility", WARN, f"{scan.imgs_without_alt} image(s) without alt text", path))
        for src in scan.external_scripts:
            out.append(CheckResult("external_scripts", WARN, f"script loaded from another site: {src[:120]}", path))
        for tag, url in scan.refs:
            if url.startswith("http://"):
                out.append(CheckResult("insecure_links", WARN, f"insecure http:// link: {url[:120]}", path))
            target = _resolve(path, url)
            if target is not None and target not in files:
                out.append(CheckResult("local_references", FAIL, f"<{tag}> points to missing file: {url[:120]}", path))
    for name, ok_msg in (("html_structure", "HTML pages are well formed"), ("local_references", "all local links resolve")):
        if not any(r.check == name for r in out):
            out.append(CheckResult(name, PASS, ok_msg))

    out += _js_syntax(files)
    return out


def passed(results: list[CheckResult]) -> bool:
    return not any(r.status == FAIL for r in results)
