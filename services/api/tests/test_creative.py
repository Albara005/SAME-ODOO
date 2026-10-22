"""Phase 6: Creative Studio - colour intelligence, real vector logos, brand kits, design agent,
and the brand applied to an App Studio project."""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from novara.ai.provider import ScriptedProvider, text_reply, tool_reply
from novara.creative import color as C
from novara.creative.logo import Colors, LogoError, LogoSpec, default_monogram, render, shape

OWNER, DEV = "owner@brand.example.com", "dev@brand.example.com"
SVG_NS = "{http://www.w3.org/2000/svg}"


# ---------------------------------------------------------------- colour


def test_contrast_matches_wcag_reference_values():
    assert round(C.contrast("#ffffff", "#000000"), 2) == 21.0
    assert round(C.contrast("#777777", "#ffffff"), 2) == 4.48   # the classic "just fails AA" grey
    assert C.contrast("#1f6feb", "#1f6feb") == 1.0


def test_oklch_round_trip_is_exact():
    for h in ("#1f6feb", "#ffd400", "#0b1f3a", "#e11d48", "#ffffff", "#000000"):
        assert C.oklch_to_hex(*C.rgb_to_oklch(C.parse_hex(h))) == h


@pytest.mark.parametrize("brand", ["#1f6feb", "#ffd400", "#0b1f3a", "#ffffff", "#000000", "#b6f5d8", "#e11d48"])
def test_every_generated_system_passes_its_contrast_checks(brand):
    system = C.build_system(brand, "#fef08a")
    assert system["all_checks_pass"], [c for c in system["checks"] if not c["passed"]]
    assert system["primary"] == brand                       # the brand colour itself is kept
    light = system["modes"]["light"]
    assert C.contrast(light["color-on-primary"], light["color-primary"]) >= 4.5
    assert C.contrast(light["color-primary"], light["color-bg"]) >= 3.0


def test_unreadable_brand_colours_are_adjusted_and_explained():
    yellow = C.build_system("#ffd400")
    assert yellow["modes"]["light"]["color-primary"] != "#ffd400"   # too light for a button on white
    assert yellow["modes"]["dark"]["color-primary"] == "#ffd400"    # fine on dark
    assert any("#ffd400" in n for n in yellow["notes"])


def test_scales_go_light_to_dark():
    p = C.build_system("#1f6feb")["scales"]["primary"]
    lum = [C.luminance(p[s]) for s in C.STEPS]
    assert lum == sorted(lum, reverse=True)


def test_css_has_light_dark_and_explicit_theme():
    css = C.to_css(C.build_system("#1f6feb"))
    assert ":root {" in css and "@media (prefers-color-scheme: dark)" in css and ':root[data-theme="dark"]' in css
    assert "--color-primary: #1f6feb;" in css and "--primary-500:" in css


def test_bad_colours_are_rejected():
    with pytest.raises(C.ColorError):
        C.parse_hex("blue")


# ---------------------------------------------------------------- logos


COLORS = Colors(mark="#1f6feb", on_mark="#ffffff", ink="#111418", muted="#57606a")


@pytest.mark.parametrize("spec", [
    LogoSpec(name="النخبة", tagline="للحلول التقنية", layout="horizontal", symbol="hexagon"),
    LogoSpec(name="Nokhba Tech", layout="horizontal", symbol="circle"),
    LogoSpec(name="متجر Nokhba", layout="stacked", symbol="arch"),
    LogoSpec(name="Nokhba", layout="wordmark", symbol="none", tagline="since 2026"),
    LogoSpec(name="النخبة", layout="icon", symbol="diamond"),
])
def test_logos_are_valid_self_contained_svg(spec):
    svg = render(spec, COLORS, variant="icon" if spec.layout == "icon" else "full")
    root = ET.fromstring(svg)                                            # well-formed XML
    assert root.tag == f"{SVG_NS}svg" and root.get("viewBox")
    tags = {el.tag.replace(SVG_NS, "") for el in root.iter()}
    assert tags <= {"svg", "title", "path", "circle", "rect", "polygon"}   # outlines only: no text, script, image
    assert root.find(f"{SVG_NS}title").text.startswith(spec.name)
    assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")    # no external references


def test_arabic_text_is_shaped_not_isolated_letters():
    joined = shape("النخبة", 72)
    isolated = shape(" ".join("النخبة"), 72)
    assert joined.width < isolated.width * 0.8   # joined letters are narrower than the same letters apart


def test_logo_inputs_are_escaped_and_validated():
    svg = render(LogoSpec(name="A&B <Co>"), COLORS)
    assert "<title id=\"logo-title\">A&amp;B &lt;Co&gt;</title>" in svg
    with pytest.raises(LogoError):
        render(LogoSpec(name="日本"), COLORS)       # the bundled font has no glyphs for it: refused, not broken
    with pytest.raises(ValueError):
        LogoSpec(name="bad‮name")             # control characters (e.g. bidi overrides)


def test_default_monogram_skips_the_arabic_article():
    assert default_monogram("النخبة") == "ن" and default_monogram("Nokhba Tech") == "N"


# ---------------------------------------------------------------- brand kits via the API


@pytest.fixture
def kit(api, app):
    api.signup(OWNER)
    api.signup(DEV)
    org = api.create_org(OWNER, "brand-co")
    api.add_member(OWNER, org, DEV, "developer")
    app.state.ai_provider = ScriptedProvider()
    r = api.req("POST", OWNER, f"/orgs/{org}/creative/brand-kits",
                json={"name": "النخبة", "tagline": "للحلول التقنية", "primary": "#1f6feb", "secondary": "#f59e0b"})
    assert r.status_code == 201, r.text
    return {"org": org, "kit": r.json()["id"], "ai": app.state.ai_provider}


def k_url(k, rest=""):
    return f"/orgs/{k['org']}/creative/brand-kits/{k['kit']}{rest}"


def test_changing_the_brand_colour_updates_every_token_and_logo(api, kit):
    api.req("POST", OWNER, k_url(kit, "/logos"), json={"name": "النخبة", "symbol": "hexagon"})
    logo = api.req("GET", OWNER, k_url(kit)).json()["logos"][0]
    before_css = api.req("GET", OWNER, k_url(kit, "/tokens.css")).text
    before_svg = api.req("GET", OWNER, k_url(kit, f"/logos/{logo['id']}.svg")).text
    assert 'fill="#1f6feb"' in before_svg

    api.req("PATCH", OWNER, k_url(kit), json={"primary": "#0f766e"})
    after_css = api.req("GET", OWNER, k_url(kit, "/tokens.css")).text
    after_svg = api.req("GET", OWNER, k_url(kit, f"/logos/{logo['id']}.svg")).text
    assert "--color-primary: #0f766e;" in after_css and before_css != after_css
    assert 'fill="#0f766e"' in after_svg and "#1f6feb" not in after_svg


def test_png_and_export_kit(api, kit):
    api.req("POST", OWNER, k_url(kit, "/logos"), json={"name": "النخبة", "tagline": "للحلول التقنية"})
    logo = api.req("GET", OWNER, k_url(kit)).json()["logos"][0]
    png = api.req("GET", OWNER, k_url(kit, f"/logos/{logo['id']}.png"), params={"width": 512})
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")

    z = zipfile.ZipFile(io.BytesIO(api.req("GET", DEV, k_url(kit, "/export.zip")).content))
    names = set(z.namelist())
    assert {"logo.svg", "logo-icon.svg", "logo-mono.svg", "logo-inverse.svg", "tokens.css", "tokens.json",
            "brand-guide.md", "logo.png", "icon-32.png", "icon-512.png"} <= names
    guide = z.read("brand-guide.md").decode()
    assert "Contrast checks" in guide and "SIL Open Font License" in guide
    inverse = z.read("logo-inverse.svg").decode()
    assert 'fill="#ffffff"' in inverse                                   # light ink for dark backgrounds


def test_brand_applies_to_an_app_without_touching_user_css(api, kit):
    project = api.req("POST", OWNER, f"/orgs/{kit['org']}/studio/projects", json={"name": "Shop", "slug": "shop"}).json()
    css_url = f"/orgs/{kit['org']}/studio/projects/{project['id']}/files/styles.css"
    original = api.req("GET", OWNER, css_url).text
    assert api.req("POST", OWNER, k_url(kit, f"/apply/{project['id']}")).status_code == 200
    api.req("PATCH", OWNER, k_url(kit), json={"primary": "#7c3aed"})
    api.req("POST", OWNER, k_url(kit, f"/apply/{project['id']}"))           # re-apply replaces, not appends
    css = api.req("GET", OWNER, css_url).text
    assert css.startswith(original.rstrip("\n"))                             # the user's CSS is untouched
    assert css.count("novara:brand:start") == 1 and "--color-primary: #7c3aed;" in css
    checks = api.req("POST", OWNER, f"/orgs/{kit['org']}/studio/projects/{project['id']}/checks").json()
    assert all(c["status"] != "fail" for c in checks)
    assert api.req("POST", DEV, k_url(kit, f"/apply/{project['id']}")).status_code == 200   # developer: apps.write


def test_design_agent_proposes_concepts_through_the_vocabulary_only(api, kit):
    kit["ai"].script = [
        tool_reply("preview_palette", {"primary": "#0f766e"}, tool_use_id="t1"),
        tool_reply("propose_logo", {"name": "النخبة", "layout": "horizontal", "symbol": "hexagon",
                                    "rationale": "Hexagon suggests structure."}, tool_use_id="t2"),
        tool_reply("propose_logo", {"name": "النخبة", "layout": "stacked", "symbol": "arch", "monogram": "ن"},
                   tool_use_id="t3"),
        tool_reply("propose_logo", {"name": "النخبة", "layout": "spiral"}, tool_use_id="t4"),   # not in vocabulary
        text_reply("Two concepts: a hexagon mark and a stacked arch."),
    ]
    out = api.req("POST", OWNER, k_url(kit, "/concepts"), json={"brief": "شركة تقنية موثوقة"}).json()
    assert len(out["concepts"]) == 2
    assert out["concepts"][0]["source"] == "assistant" and out["concepts"][0]["note"] == "Hexagon suggests structure."
    palette = kit["ai"].requests[1]["messages"][-1]["content"][0]["content"]
    assert "all_checks_pass" in palette
    assert kit["ai"].requests[4]["messages"][-1]["content"][0].get("is_error") is True   # spiral refused
    assert {t["name"] for t in kit["ai"].requests[0]["tools"]} == {"preview_palette", "propose_logo"}


def test_brand_permissions(api, kit):
    assert api.req("GET", DEV, k_url(kit)).status_code == 200
    assert api.req("PATCH", DEV, k_url(kit), json={"primary": "#000000"}).status_code == 403
    assert api.req("POST", DEV, k_url(kit, "/concepts"), json={"brief": "x"}).status_code == 403


def test_logo_text_keeps_numbers_and_mixed_scripts_in_reading_order():
    """Bidi in a logo line: "منذ 1985" must not be drawn as "منذ 5891"."""
    from novara.creative.logo import _runs

    def visual(text: str) -> str:  # left-to-right order of characters as drawn
        runs, base_rtl = _runs(text)
        return "".join(t[::-1] if rtl else t for t, rtl in (reversed(runs) if base_rtl else runs))

    assert visual("منذ 1985") == "1985 ذنم"
    assert visual("متجر 24/7 للتوصيل") == "ليصوتلل 24/7 رجتم"
    assert visual("الرياض - KSA 2030") == "KSA 2030 - ضايرلا"
    assert visual("Shop متجر 24") == "Shop رجتم 24"
    assert visual("Bakery 1985") == "Bakery 1985"
