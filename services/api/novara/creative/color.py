"""Colour Intelligence: a complete, accessible colour system from one brand colour.

* Scales are built in OKLCH, so steps look evenly spaced to the eye, and every colour is
  pulled back into the sRGB gamut by lowering chroma (never by clipping channels).
* Text-on-background colours are chosen by measured WCAG 2.x contrast, not by guess:
  body text >= 4.5:1, large text and UI parts (focus ring, button fill) >= 3:1.
* The brand colour itself is kept; when it can't carry text at the required contrast, the
  nearest step of its own scale that can is used for that role, and the report says so.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
_LIGHTNESS = dict(zip(STEPS, (0.975, 0.94, 0.88, 0.80, 0.71, 0.62, 0.54, 0.46, 0.38, 0.30, 0.22)))
STATUS_HUES = {"success": 150.0, "warning": 75.0, "danger": 27.0, "info": 245.0}
AA_TEXT, AA_LARGE_UI = 4.5, 3.0
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


class ColorError(ValueError):
    pass


# ---------------------------------------------------------------- conversions


def parse_hex(value: str) -> tuple[float, float, float]:
    m = _HEX.match(value.strip())
    if not m:
        raise ColorError(f"not a hex colour: {value!r} (use #RRGGBB)")
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def rgb_to_oklch(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (_to_linear(c) for c in rgb)
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def oklch_to_linear(L: float, C: float, H: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def oklch_to_hex(L: float, C: float, H: float) -> str:
    """Convert, reducing chroma until the colour fits in sRGB (keeps lightness and hue)."""
    lo, hi = 0.0, C
    lin = oklch_to_linear(L, C, H)
    if all(-1e-6 <= c <= 1 + 1e-6 for c in lin):
        return to_hex(tuple(_from_linear(min(1, max(0, c))) for c in lin))
    for _ in range(24):  # binary search for the largest in-gamut chroma
        mid = (lo + hi) / 2
        if all(-1e-6 <= c <= 1 + 1e-6 for c in oklch_to_linear(L, mid, H)):
            lo = mid
        else:
            hi = mid
    return to_hex(tuple(_from_linear(min(1, max(0, c))) for c in oklch_to_linear(L, lo, H)))


# ---------------------------------------------------------------- contrast


def luminance(hex_color: str) -> float:
    r, g, b = (_to_linear(c) for c in parse_hex(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def best_on(bg: str, light: str = "#ffffff", dark: str = "#111418") -> str:
    """The text colour (light or dark) with the higher contrast on ``bg``."""
    return light if contrast(light, bg) >= contrast(dark, bg) else dark


# ---------------------------------------------------------------- system


def scale(hex_color: str, *, chroma: float | None = None) -> dict[int, str]:
    L0, C0, H = rgb_to_oklch(parse_hex(hex_color))
    C = C0 if chroma is None else chroma
    out = {}
    for step, L in _LIGHTNESS.items():
        # Very light and very dark steps carry less chroma, as in hand-made palettes.
        taper = 1 - abs(L - 0.6) * 0.9
        out[step] = oklch_to_hex(L, max(0.0, C * max(0.25, taper)), H)
    return out


@dataclass(frozen=True)
class Check:
    pair: str
    mode: str
    ratio: float
    required: float

    @property
    def passed(self) -> bool:
        return self.ratio >= self.required

    def as_dict(self) -> dict:
        return {"pair": self.pair, "mode": self.mode, "ratio": round(self.ratio, 2), "required": self.required,
                "passed": self.passed}


def _accessible_step(brand: str, steps: dict[int, str], against: str, required: float, prefer_dark: bool) -> tuple[str, bool]:
    """The brand colour if it meets ``required`` as text on ``against``, else the nearest scale step that does."""
    if contrast(brand, against) >= required:
        return brand, False
    order = [s for s in STEPS if s >= 500] if prefer_dark else [s for s in reversed(STEPS) if s <= 500]
    for s in order:
        if contrast(steps[s], against) >= required:
            return steps[s], True
    return steps[950 if prefer_dark else 50], True


def _fill_for(brand: str, steps: dict[int, str], bg: str) -> str:
    """A button fill: carries text at 4.5:1 and stands out from the background at 3:1 (WCAG 1.4.11).
    The brand colour itself when it qualifies, else the closest step of its scale that does."""
    def ok(c: str) -> bool:
        return contrast(best_on(c), c) >= AA_TEXT and contrast(c, bg) >= AA_LARGE_UI

    if ok(brand):
        return brand
    target = rgb_to_oklch(parse_hex(brand))[0]
    candidates = sorted(STEPS, key=lambda st: abs(_LIGHTNESS[st] - target))
    return next((steps[st] for st in candidates if ok(steps[st])), steps[700])


def build_system(primary: str, secondary: str | None = None) -> dict:
    primary = to_hex(parse_hex(primary))
    p = scale(primary)
    s = scale(secondary) if secondary else None
    hue = rgb_to_oklch(parse_hex(primary))[2]
    neutral = scale(oklch_to_hex(0.6, 0.012, hue), chroma=0.012)  # greys tinted with the brand hue
    status = {name: scale(oklch_to_hex(0.6, 0.16, h), chroma=0.16) for name, h in STATUS_HUES.items()}
    notes: list[str] = []
    # The same adjustments as data, so each client can word them in its own language.
    adjustments: list[dict] = []

    modes = {}
    for mode in ("light", "dark"):
        bg = neutral[50] if mode == "light" else neutral[950]
        surface = "#ffffff" if mode == "light" else neutral[900]
        text = neutral[950] if mode == "light" else neutral[50]
        muted = neutral[700] if mode == "light" else neutral[300]
        fill = _fill_for(primary, p, bg)
        if fill != primary:
            notes.append(f"{mode}: buttons use {fill} instead of {primary} so they stand out and their text stays readable")
            adjustments.append({"kind": "button", "mode": mode, "original": primary, "used": fill})
        link, adjusted = _accessible_step(primary, p, bg, AA_TEXT, prefer_dark=(mode == "light"))
        if adjusted:
            notes.append(f"{mode}: brand colour {primary} is too {'light' if mode == 'light' else 'dark'} for text "
                         f"on the background; links and text accents use {link} instead")
            adjustments.append({"kind": "text", "mode": mode, "original": primary, "used": link})
        tokens = {
            "color-bg": bg, "color-surface": surface, "color-surface-raised": neutral[100] if mode == "light" else neutral[800],
            "color-text": text, "color-text-muted": muted,
            "color-border": neutral[200] if mode == "light" else neutral[700],
            "color-primary": fill, "color-on-primary": best_on(fill),
            "color-primary-hover": p[700] if mode == "light" else p[300],
            "color-primary-subtle": p[100] if mode == "light" else p[900],
            "color-link": link,
            "color-focus-ring": _accessible_step(primary, p, bg, AA_LARGE_UI, prefer_dark=(mode == "light"))[0],
        }
        if s:
            secondary_fill = _fill_for(to_hex(parse_hex(secondary)), s, bg)
            tokens |= {"color-secondary": secondary_fill, "color-on-secondary": best_on(secondary_fill)}
        for name, sc in status.items():
            fill_s = sc[600] if mode == "light" else sc[400]
            tokens |= {f"color-{name}": fill_s, f"color-on-{name}": best_on(fill_s),
                       f"color-{name}-text": _accessible_step(sc[500], sc, bg, AA_TEXT, prefer_dark=(mode == "light"))[0]}
        modes[mode] = tokens

    checks = []
    for mode, t in modes.items():
        pairs = [("color-text on color-bg", t["color-text"], t["color-bg"], AA_TEXT),
                 ("color-text on color-surface", t["color-text"], t["color-surface"], AA_TEXT),
                 ("color-text-muted on color-bg", t["color-text-muted"], t["color-bg"], AA_TEXT),
                 ("color-on-primary on color-primary", t["color-on-primary"], t["color-primary"], AA_TEXT),
                 ("color-primary on color-bg (button edge)", t["color-primary"], t["color-bg"], AA_LARGE_UI),
                 ("color-link on color-bg", t["color-link"], t["color-bg"], AA_TEXT),
                 ("color-focus-ring on color-bg", t["color-focus-ring"], t["color-bg"], AA_LARGE_UI)]
        pairs += [(f"color-on-{n} on color-{n}", t[f"color-on-{n}"], t[f"color-{n}"], AA_TEXT) for n in STATUS_HUES]
        pairs += [(f"color-{n}-text on color-bg", t[f"color-{n}-text"], t["color-bg"], AA_TEXT) for n in STATUS_HUES]
        if "color-secondary" in t:
            pairs.append(("color-on-secondary on color-secondary", t["color-on-secondary"], t["color-secondary"], AA_TEXT))
        checks += [Check(name, mode, contrast(fg, bg), req) for name, fg, bg, req in pairs]

    return {
        "primary": primary,
        "secondary": to_hex(parse_hex(secondary)) if secondary else None,
        "scales": {"primary": p, "neutral": neutral, **({"secondary": s} if s else {}), **status},
        "modes": modes,
        "checks": [c.as_dict() for c in checks],
        "all_checks_pass": all(c.passed for c in checks),
        "notes": notes,
        "adjustments": adjustments,
    }


def to_css(system: dict, *, extra: dict[str, str] | None = None) -> str:
    """CSS custom properties: light by default, dark via prefers-color-scheme or data-theme."""
    def block(tokens: dict[str, str]) -> str:
        return "\n".join(f"  --{k}: {v};" for k, v in tokens.items())

    light = system["modes"]["light"] | (extra or {})
    scales = {f"{name}-{step}": hexv for name, sc in system["scales"].items() for step, hexv in sc.items()}
    return (
        "/* Generated by NOVARA Creative Studio. Change the brand colour and regenerate. */\n"
        f":root {{\n{block(light)}\n{block(scales)}\n}}\n"
        f"@media (prefers-color-scheme: dark) {{\n  :root:not([data-theme=\"light\"]) {{\n"
        + "\n".join("  " + line for line in block(system["modes"]["dark"]).splitlines())
        + "\n  }\n}\n"
        f":root[data-theme=\"dark\"] {{\n{block(system['modes']['dark'])}\n}}\n"
    )
