"""Vector logos rendered by the platform from a constrained spec.

The AI (or the user) chooses from a small design vocabulary - layout, symbol shape,
monogram, weight, colours - and this module draws it. The output is real vector SVG:
text is shaped with HarfBuzz (correct joined Arabic forms) and converted to outlines with
the bundled Noto Kufi Arabic font (SIL OFL), so the file looks the same everywhere, with
or without fonts installed. The SVG holds only paths and shapes: no <text>, no scripts,
no external references. PNG export rasterises that SVG when cairosvg is available.
"""

from __future__ import annotations

import html
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from pydantic import BaseModel, Field, field_validator

FONT_DIR = Path(__file__).parent / "fonts"
FONT_FILES = {400: FONT_DIR / "NotoKufiArabic_400Regular.ttf", 700: FONT_DIR / "NotoKufiArabic_700Bold.ttf"}

Layout = Literal["horizontal", "stacked", "wordmark", "icon"]
Symbol = Literal["circle", "rounded_square", "hexagon", "diamond", "arch", "none"]


class LogoError(ValueError):
    pass


class LogoSpec(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    tagline: str = Field(default="", max_length=60)
    layout: Layout = "horizontal"
    symbol: Symbol = "circle"
    monogram: str = Field(default="", max_length=2, description="1-2 letters inside the symbol; default: first letter")
    weight: Literal[400, 700] = 700

    @field_validator("name", "tagline", "monogram")
    @classmethod
    def _printable(cls, v: str) -> str:
        if any(unicodedata.category(c).startswith("C") for c in v):
            raise ValueError("control characters are not allowed")
        return v.strip()


@dataclass(frozen=True)
class Shaped:
    path: str       # SVG path data, origin at the left of the baseline
    width: float
    ascent: float
    descent: float  # positive number below the baseline
    bounds: tuple[float, float, float, float]  # ink box (x0, y0, x1, y1) in SVG coordinates


@lru_cache(maxsize=4)
def _font(weight: int):
    path = FONT_FILES[weight]
    tt = TTFont(path)
    face = hb.Face(hb.Blob.from_file_path(str(path)))
    return tt, hb.Font(face), tt.getGlyphSet(), tt.getGlyphOrder(), tt["head"].unitsPerEm, tt["hhea"]


def _is_rtl(ch: str) -> bool:
    return unicodedata.bidirectional(ch) in ("R", "AL")


def _kind(ch: str) -> str | None:
    """'rtl' (Arabic letters), 'ltr' (Latin letters), 'num' (digits) or None for neutrals."""
    b = unicodedata.bidirectional(ch)
    if b in ("R", "AL"):
        return "rtl"
    if b == "L":
        return "ltr"
    if b in ("EN", "AN"):
        return "num"
    return None


def _runs(text: str) -> tuple[list[tuple[str, bool]], bool]:
    """Split into direction runs (text, is_rtl), following the parts of the Unicode bidi algorithm a
    one-line logo needs: digits read left to right ("منذ 1985" keeps 1985 in order), digits after
    Latin text join it ("KSA 2030"), and spaces between runs of different direction stay between them."""
    runs: list[list] = []
    for ch in text:
        kind = _kind(ch)
        if runs and (kind is None or runs[-1][1] in (kind, None) or (kind == "num" and runs[-1][1] == "ltr")):
            runs[-1][0] += ch
            runs[-1][1] = runs[-1][1] or kind
        else:
            runs.append([ch, kind])
    base_rtl = next((k == "rtl" for k in map(_kind, text) if k in ("rtl", "ltr")), False)
    for r in runs:
        r[1] = r[1] == "rtl" if r[1] is not None else base_rtl
    # Trailing neutrals of a run against the line's direction belong between it and the next run.
    for this, nxt in zip(runs, runs[1:]):
        if this[1] != base_rtl:
            body = this[0].rstrip(" -–—.,:;/")
            if body:
                this[0], nxt[0] = body, this[0][len(body):] + nxt[0]
    return [(t, rtl) for t, rtl in runs], base_rtl


def shape(text: str, size: float, weight: int = 700) -> Shaped:
    tt, font, glyphs, order, upem, hhea = _font(weight)
    missing = [c for c in text if not c.isspace() and ord(c) not in tt.getBestCmap()]
    if missing:
        raise LogoError(f"the logo font has no glyph for: {''.join(sorted(set(missing)))}")
    scale = size / upem
    runs, base_rtl = _runs(text)
    if base_rtl:
        runs = list(reversed(runs))  # visual order of runs for a right-to-left line
    pen = SVGPathPen(glyphs)
    ink = BoundsPen(glyphs)
    x = 0.0
    for run, rtl in runs:
        buf = hb.Buffer()
        buf.add_str(run.strip() if len(runs) == 1 else run)
        buf.direction = "rtl" if rtl else "ltr"
        buf.guess_segment_properties()
        hb.shape(font, buf, {"kern": True, "liga": True})
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):  # HarfBuzz returns visual order
            gx, gy = x + pos.x_offset * scale, pos.y_offset * scale
            transform = (scale, 0, 0, -scale, gx, -gy)
            glyphs[order[info.codepoint]].draw(TransformPen(pen, transform))
            glyphs[order[info.codepoint]].draw(TransformPen(ink, transform))
            x += pos.x_advance * scale
    return Shaped(path=pen.getCommands(), width=x, ascent=hhea.ascent * scale, descent=-hhea.descent * scale,
                  bounds=ink.bounds or (0.0, 0.0, x, 0.0))


def default_monogram(name: str) -> str:
    """First letter of the name, skipping the Arabic article "ال" (النخبة -> ن)."""
    word = name.strip().split()[0] if name.strip() else "?"
    if word.startswith("ال") and len(word) > 2:
        word = word[2:]
    return word[0]


def _symbol_svg(kind: str, x: float, y: float, s: float, fill: str) -> str:
    cx, cy, r = x + s / 2, y + s / 2, s / 2
    if kind == "circle":
        return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}"/>'
    if kind == "rounded_square":
        return f'<rect x="{x:.2f}" y="{y:.2f}" width="{s:.2f}" height="{s:.2f}" rx="{s * 0.22:.2f}" fill="{fill}"/>'
    if kind == "hexagon":
        pts = [(cx + r * 0.98 * c, cy + r * 0.98 * d) for c, d in
               ((0, -1), (0.866, -0.5), (0.866, 0.5), (0, 1), (-0.866, 0.5), (-0.866, -0.5))]
        return f'<polygon points="{" ".join(f"{a:.2f},{b:.2f}" for a, b in pts)}" fill="{fill}"/>'
    if kind == "diamond":
        pts = [(cx, y), (x + s, cy), (cx, y + s), (x, cy)]
        return f'<polygon points="{" ".join(f"{a:.2f},{b:.2f}" for a, b in pts)}" fill="{fill}"/>'
    if kind == "arch":
        return (f'<path d="M{x:.2f},{y + s:.2f} L{x:.2f},{cy:.2f} A{r:.2f},{r:.2f} 0 0 1 {x + s:.2f},{cy:.2f} '
                f'L{x + s:.2f},{y + s:.2f} Z" fill="{fill}"/>')
    return ""


def _place(shaped: Shaped, x: float, baseline: float, fill: str) -> str:
    return f'<path transform="translate({x:.2f},{baseline:.2f})" d="{shaped.path}" fill="{fill}"/>'


@dataclass(frozen=True)
class Colors:
    mark: str       # symbol fill
    on_mark: str    # monogram on the symbol
    ink: str        # name and tagline
    muted: str      # tagline


def render(spec: LogoSpec, colors: Colors, *, variant: str = "full") -> str:
    """variant: full (the spec's layout) | icon (symbol + monogram, square)."""
    layout = "icon" if variant == "icon" else spec.layout
    symbol = spec.symbol if spec.symbol != "none" or layout != "icon" else "rounded_square"
    pad, s = 24.0, 120.0
    parts: list[str] = []
    mono_text = spec.monogram or default_monogram(spec.name)

    def monogram(x: float, y: float) -> None:
        m = shape(mono_text, s * 0.46, spec.weight)
        x0, y0, x1, y1 = m.bounds  # centre the letter's actual ink, not its font box
        cy = y + s * (0.56 if symbol == "arch" else 0.5)  # an arch's visual centre sits lower
        parts.append(_place(m, x + s / 2 - (x0 + x1) / 2, cy - (y0 + y1) / 2, colors.on_mark))

    if layout == "icon":
        w = h = s + 2 * pad
        parts.append(_symbol_svg(symbol, pad, pad, s, colors.mark))
        monogram(pad, pad)
    else:
        name = shape(spec.name, 72.0, spec.weight)
        tag = shape(spec.tagline, 26.0, 400) if spec.tagline else None
        text_h = name.ascent * 0.8 + (tag.ascent + 8 if tag else 0)
        text_w = max(name.width, tag.width if tag else 0)
        has_symbol = symbol != "none" and layout != "wordmark"
        rtl = any(_is_rtl(c) for c in spec.name)
        if layout == "stacked":
            w = max(text_w, s if has_symbol else 0) + 2 * pad
            y = pad
            if has_symbol:
                parts.append(_symbol_svg(symbol, (w - s) / 2, y, s, colors.mark))
                monogram((w - s) / 2, y)
                y += s + 20
            parts.append(_place(name, (w - name.width) / 2, y + name.ascent * 0.8, colors.ink))
            y += name.ascent * 0.8 + name.descent * 0.6
            if tag:
                parts.append(_place(tag, (w - tag.width) / 2, y + tag.ascent * 0.8, colors.muted))
                y += tag.ascent * 0.8 + tag.descent
            h = y + pad
        else:  # horizontal or wordmark
            gap = 28.0 if has_symbol else 0.0
            sym_w = s if has_symbol else 0.0
            w = sym_w + gap + text_w + 2 * pad
            h = max(sym_w, text_h + name.descent * 0.6) + 2 * pad
            # The symbol sits where reading starts: right for Arabic names, left otherwise.
            sym_x = w - pad - sym_w if rtl else pad
            text_x = pad if rtl else pad + sym_w + gap
            if has_symbol:
                parts.append(_symbol_svg(symbol, sym_x, (h - s) / 2, s, colors.mark))
                monogram(sym_x, (h - s) / 2)
            top = (h - text_h - name.descent * 0.6) / 2
            align = (lambda width: text_x + text_w - width) if rtl else (lambda width: text_x)
            parts.append(_place(name, align(name.width), top + name.ascent * 0.8, colors.ink))
            if tag:
                parts.append(_place(tag, align(tag.width), top + name.ascent * 0.8 + name.descent * 0.6 + tag.ascent * 0.8,
                                    colors.muted))
    title = html.escape(spec.name + (f" - {spec.tagline}" if spec.tagline else ""))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'role="img" aria-labelledby="logo-title"><title id="logo-title">{title}</title>{"".join(parts)}</svg>')


def png(svg: str, width: int) -> bytes:
    try:
        import cairosvg
    except Exception as e:  # noqa: BLE001 - library or its system dependency (libcairo) missing
        raise LogoError("PNG export isn't available on this server (cairosvg/libcairo not installed)") from e
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width)
