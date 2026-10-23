"""Brand kit operations: colour system, logo variants, export, applying to App Studio projects."""

from __future__ import annotations

import io
import json
import re
import zipfile

from novara.creative import color as C
from novara.creative import logo as L
from novara.creative.models import BrandKit, BrandLogo

# Web font stacks for apps (the logo itself is outlined, so it needs no font at all).
FONT_PAIRS: dict[str, dict[str, str]] = {
    "kufi": {"heading": '"Noto Kufi Arabic", "Segoe UI", Tahoma, sans-serif',
             "body": '"Noto Kufi Arabic", "Segoe UI", Tahoma, sans-serif'},
    "plex": {"heading": '"IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif',
             "body": '"IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif'},
    "naskh": {"heading": '"Noto Kufi Arabic", "Segoe UI", Tahoma, sans-serif',
              "body": '"Noto Naskh Arabic", Georgia, "Times New Roman", serif'},
    "system": {"heading": 'system-ui, "Segoe UI", Tahoma, sans-serif', "body": 'system-ui, "Segoe UI", Tahoma, sans-serif'},
}
BRAND_START, BRAND_END = "/* novara:brand:start - generated, re-applying replaces this block */", "/* novara:brand:end */"


def system_for(primary: str, secondary: str | None) -> dict:
    return C.build_system(primary, secondary)


def logo_colors(kit: BrandKit, variant: str) -> L.Colors:
    light, dark = kit.system["modes"]["light"], kit.system["modes"]["dark"]
    if variant == "mono":
        ink = light["color-text"]
        return L.Colors(mark=ink, on_mark="#ffffff", ink=ink, muted=ink)
    if variant == "inverse":  # for dark backgrounds
        return L.Colors(mark=dark["color-primary"], on_mark=dark["color-on-primary"], ink="#ffffff",
                        muted=dark["color-text-muted"])
    return L.Colors(mark=light["color-primary"], on_mark=light["color-on-primary"], ink=light["color-text"],
                    muted=light["color-text-muted"])


def render_logo(kit: BrandKit, logo: BrandLogo, variant: str = "full") -> str:
    spec = L.LogoSpec(**logo.spec)
    return L.render(spec, logo_colors(kit, variant), variant="icon" if variant == "icon" else "full")


def css(kit: BrandKit) -> str:
    fonts = FONT_PAIRS[kit.font_pair]
    return C.to_css(kit.system, extra={"font-heading": fonts["heading"], "font-body": fonts["body"],
                                       # aliases for the App Studio static template's token names
                                       "color-muted": "var(--color-text-muted)", "font": "var(--font-body)"})


def tokens_json(kit: BrandKit) -> dict:
    return {"name": kit.name, "primary": kit.system["primary"], "secondary": kit.system["secondary"],
            "fonts": FONT_PAIRS[kit.font_pair], "modes": kit.system["modes"], "scales": kit.system["scales"]}


def guide(kit: BrandKit, logos: list[BrandLogo]) -> str:
    light = kit.system["modes"]["light"]
    rows = "\n".join(f"| `{c['pair']}` | {c['mode']} | {c['ratio']}:1 | {c['required']}:1 | {'✅' if c['passed'] else '❌'} |"
                     for c in kit.system["checks"])
    notes = "\n".join(f"- {n}" for n in kit.system["notes"]) or "- None: the brand colours meet every check as they are."
    return f"""# {kit.name} - Brand guide

{kit.tagline}

## Colours

| Role | Light | Dark |
|---|---|---|
""" + "\n".join(f"| {k} | `{v}` | `{kit.system['modes']['dark'][k]}` |" for k, v in light.items()) + f"""

Brand colour: `{kit.system['primary']}`""" + (f", secondary `{kit.system['secondary']}`" if kit.system["secondary"] else "") + f"""

### Accessibility adjustments
{notes}

### Contrast checks (WCAG 2.x)
| Pair | Mode | Ratio | Needed | |
|---|---|---|---|---|
{rows}

## Typography
- Headings: {FONT_PAIRS[kit.font_pair]['heading']}
- Body: {FONT_PAIRS[kit.font_pair]['body']}

## Logo
- Files: `logo.svg` (main), `logo-icon.svg` (square: app icons, favicons, avatars), `logo-mono.svg` (one colour),
  `logo-inverse.svg` (on dark backgrounds). PNG files are the same artwork rasterised.
- The SVG files are true vectors with the lettering converted to outlines: they need no fonts.
- Keep clear space around the logo of at least the height of the symbol's monogram.
- Minimum size: 24 px tall for the icon, 120 px wide for the full logo.
- Don't stretch, recolour outside the palette, add effects, or retype the name in another font.

Logo lettering is drawn from Noto Kufi Arabic (SIL Open Font License 1.1).
{len(logos)} logo concept(s) in this kit.
"""


def export_zip(kit: BrandKit, logos: list[BrandLogo]) -> bytes:
    buf = io.BytesIO()
    main = next((l for l in logos if l.primary), logos[0] if logos else None)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("tokens.css", css(kit))
        z.writestr("tokens.json", json.dumps(tokens_json(kit), ensure_ascii=False, indent=2))
        z.writestr("brand-guide.md", guide(kit, logos))
        if main is not None:
            for variant, name in (("full", "logo"), ("icon", "logo-icon"), ("mono", "logo-mono"),
                                  ("inverse", "logo-inverse")):
                svg = render_logo(kit, main, variant)
                z.writestr(f"{name}.svg", svg)
                try:
                    z.writestr(f"{name}.png", L.png(svg, 1024))
                    if variant == "icon":
                        for size in (512, 192, 32):
                            z.writestr(f"icon-{size}.png", L.png(svg, size))
                except L.LogoError:
                    z.writestr("PNG-NOT-INCLUDED.txt", "This server can't rasterise SVG (cairosvg/libcairo missing). "
                                                       "The SVG files are complete; export PNGs from them.\n")
    return buf.getvalue()


def apply_to_stylesheet(existing: str, kit: BrandKit) -> str:
    """Add or replace the brand block at the end of a stylesheet. Later rules win, so the brand
    overrides the template's defaults while the user's own CSS stays untouched."""
    block = f"{BRAND_START}\n{css(kit)}{BRAND_END}\n"
    pattern = re.compile(re.escape(BRAND_START) + r".*?" + re.escape(BRAND_END) + r"\n?", re.S)
    if pattern.search(existing):
        return pattern.sub(lambda _: block, existing)
    return existing.rstrip("\n") + "\n\n" + block
