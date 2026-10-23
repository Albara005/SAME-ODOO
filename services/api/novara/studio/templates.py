"""Project templates. Each defines the starter files, allowed file types and its checks.

Starting with one well-defined template keeps generated apps inside a scope the
platform can actually check; more templates (e.g. a server-side app) need an isolated
execution backend first (see docs/phase-4-app-studio.md).
"""

from __future__ import annotations

import html
from dataclasses import dataclass

STATIC_INDEX = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name}</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="site-header">
    <h1>{name}</h1>
    <p class="lead">{description}</p>
  </header>
  <main>
    <section class="card">
      <h2>مرحبًا</h2>
      <p>هذه نقطة البداية لتطبيقك. صِف للمساعد ما تريد وسيعدّل الملفات.</p>
      <button type="button" id="greet">جرّب</button>
      <p id="output" aria-live="polite"></p>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""

STATIC_CSS = """/* Design tokens: change these and the whole app follows. */
:root {
  --color-primary: #1f6feb;
  --color-on-primary: #ffffff;
  --color-bg: #f7f8fa;
  --color-surface: #ffffff;
  --color-text: #1b1f24;
  --color-muted: #57606a;
  --radius: 12px;
  --space: 16px;
  --font: "IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --color-bg: #0d1117;
    --color-surface: #161b22;
    --color-text: #e6edf3;
    --color-muted: #9da7b3;
  }
}
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; }
}
* { box-sizing: border-box; }
body { margin: 0; font-family: var(--font); background: var(--color-bg); color: var(--color-text); line-height: 1.6; }
.site-header, main { max-width: 960px; margin: 0 auto; padding: var(--space); }
.lead { color: var(--color-muted); }
.card { background: var(--color-surface); border-radius: var(--radius); padding: calc(var(--space) * 1.5); }
button {
  background: var(--color-primary); color: var(--color-on-primary); border: 0;
  border-radius: calc(var(--radius) / 2); padding: 10px 18px; font: inherit; cursor: pointer;
}
button:focus-visible { outline: 3px solid var(--color-text); outline-offset: 2px; }
"""

STATIC_JS = """document.getElementById("greet").addEventListener("click", () => {
  document.getElementById("output").textContent = "أهلًا! التطبيق يعمل.";
});
"""


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    allowed_extensions: frozenset[str]
    starter: dict[str, str]
    guidance: str


TEMPLATES: dict[str, Template] = {
    "static-site": Template(
        key="static-site",
        name="Static web app",
        description="HTML, CSS and JavaScript that runs in the browser: landing pages, catalogues, calculators, "
                    "forms. No server code.",
        allowed_extensions=frozenset({".html", ".css", ".js", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp",
                                      ".gif", ".ico", ".txt", ".md", ".woff2"}),
        starter={"index.html": STATIC_INDEX, "styles.css": STATIC_CSS, "app.js": STATIC_JS},
        guidance=(
            "Template: static web app (HTML/CSS/JS only, runs in the browser, no server code, no build step).\n"
            "- index.html is the entry point. Every HTML page needs <html lang=...> (and dir=\"rtl\" for Arabic), "
            "<meta name=\"viewport\">, and a <title>.\n"
            "- Keep colours, spacing and fonts in the CSS custom properties in styles.css (design tokens); "
            "support dark mode and prefers-reduced-motion.\n"
            "- Every <img> needs alt text. Links and src attributes must point to files that exist.\n"
            "- Don't load scripts from other sites; put code in local .js files. Never put API keys or "
            "passwords in files - the site is public.\n"
            "- Allowed file types: html, css, js, json, svg, png, jpg, webp, gif, ico, txt, md, woff2."
        ),
    ),
}


def starter_files(template: Template, *, name: str, description: str) -> dict[str, str]:
    values = {"name": html.escape(name), "description": html.escape(description)}
    return {path: body.format(**values) if path.endswith(".html") else body for path, body in template.starter.items()}
