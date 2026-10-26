"""How a brand moves: motion tokens from a personality, animations only for people who allow motion, slides that
follow the reading direction, and the motion applied to an App Studio app (checked in a real browser)."""

from __future__ import annotations

import io
import re
import zipfile

import pytest

from novara.ai.provider import text_reply, tool_reply
from novara.creative import motion as M
from tests.test_browser_checks import checks_on, needs_browser  # noqa: F401 (fixture)
from tests.test_creative import DEV, OWNER, k_url, kit  # noqa: F401 (fixture)


def test_personalities_set_the_pace_within_limits():
    calm = M.build("calm")
    assert calm["notes"] == []
    assert (calm["tokens"]["motion-fast"], calm["tokens"]["motion-slow"], calm["tokens"]["motion-distance"]) == ("160ms", "420ms", "8px")
    lively = M.build("lively")
    assert lively["tokens"]["motion-ease-enter"].startswith("cubic-bezier(0.34, 1.36")      # a little overshoot
    assert M.build("still")["tokens"]["motion-distance"] == "0px"

    slow = M.build("calm", 1.5)
    assert slow["tokens"]["motion-slow"] == "500ms" and "shortened to 500ms" in slow["notes"][0]
    fast = M.build("crisp", 0.5)
    assert fast["tokens"]["motion-fast"] == "80ms" and "lengthened to 80ms" in fast["notes"][0]
    for bad in (("bouncy", 1.0), ("calm", 3.0), ("calm", 0.1)):
        with pytest.raises(M.MotionError):
            M.build(*bad)


def _blocks(css: str) -> tuple[str, str, str]:
    """The CSS outside any media query, inside no-preference, and inside reduce."""
    allow = css.index("@media (prefers-reduced-motion: no-preference)")
    reduce = css.index("@media (prefers-reduced-motion: reduce)")
    return css[:allow], css[allow:reduce], css[reduce:]


@pytest.mark.parametrize("personality", sorted(M.PERSONALITIES))
def test_only_people_who_allow_motion_see_it(personality):
    css = M.to_css(M.build(personality))
    outside, allowed, reduced = _blocks(css)
    # Outside the media queries: tokens and keyframes only, nothing that plays.
    assert not re.search(r"\b(animation|transition)\s*:", outside)
    assert re.search(r"animation-name: motion-rise", allowed) and "transition:" in allowed
    assert "animation-duration: 1ms !important" in reduced and "transition-duration: 1ms !important" in reduced
    assert "infinite" not in css
    pulse = re.search(r"\.motion-pulse \{ animation: motion-pulse ([\d.]+)s [^;]* (\d+); \}", allowed)
    assert pulse and float(pulse.group(1)) >= 1 and int(pulse.group(2)) == 2       # slower than 1/s, and stops
    assert '[dir="rtl"] { --motion-dir: -1; }' in css and "var(--motion-dir)" in css


def test_a_kits_motion_through_the_api(api, kit):
    out = api.req("GET", OWNER, k_url(kit)).json()["motion"]
    assert (out["personality"], out["speed"], out["tokens"]["motion-slow"]) == ("calm", 1.0, "420ms")

    changed = api.req("PATCH", OWNER, k_url(kit), json={"motion_personality": "lively", "motion_speed": 1.25})
    assert changed.status_code == 200, changed.text
    motion = changed.json()["motion"]
    assert (motion["personality"], motion["tokens"]["motion-slow"], motion["tokens"]["motion-fast"]) == ("lively", "475ms", "175ms")
    assert "@media (prefers-reduced-motion: no-preference)" in motion["css"]
    assert api.req("PATCH", OWNER, k_url(kit), json={"motion_speed": 1}).json()["motion"]["personality"] == "lively"

    css = api.req("GET", DEV, k_url(kit, "/tokens.css")).text
    assert "--color-primary:" in css and "--motion-slow: 380ms;" in css and ".motion-rise" in css
    assert api.req("GET", DEV, k_url(kit, "/tokens.json")).json()["motion"]["personality"] == "lively"
    script = api.req("GET", DEV, k_url(kit, "/motion.js"))
    assert script.headers["content-type"].startswith("text/javascript") and "prefers-reduced-motion: reduce" in script.text

    z = zipfile.ZipFile(io.BytesIO(api.req("GET", DEV, k_url(kit, "/export.zip")).content))
    assert "motion.js" in z.namelist()
    guide = z.read("brand-guide.md").decode()
    assert "## Motion" in guide and "Personality: **lively**" in guide and "`motion-pulse`" in guide

    assert api.req("PATCH", OWNER, k_url(kit), json={"motion_personality": "bouncy"}).status_code == 422
    assert api.req("PATCH", OWNER, k_url(kit), json={"motion_speed": 5}).status_code == 422
    assert api.req("PATCH", DEV, k_url(kit), json={"motion_personality": "crisp"}).status_code == 403


def _apply(api, kit):
    project = api.req("POST", OWNER, f"/orgs/{kit['org']}/studio/projects", json={"name": "Shop", "slug": "shop"}).json()
    base = f"/orgs/{kit['org']}/studio/projects/{project['id']}"
    api.req("PUT", OWNER, f"{base}/files/about.html", content=(
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width"><title>عن</title><link rel="stylesheet" href="styles.css"></head>'
        '<body><h1 class="motion-rise">من نحن</h1><section data-reveal>قصتنا</section></body></html>').encode())
    for _ in range(2):                                                    # applying twice links the script once
        assert api.req("POST", OWNER, k_url(kit, f"/apply/{project['id']}")).status_code == 200
    return base


def test_the_motion_applies_to_an_app(api, kit):
    base = _apply(api, kit)
    files = {f["path"] for f in api.req("GET", OWNER, base).json()["files"]}
    assert {"motion.js", "styles.css", "index.html", "about.html"} <= files
    for page in ("index.html", "about.html"):
        html = api.req("GET", OWNER, f"{base}/files/{page}").text
        assert html.count('<script src="motion.js" defer></script>') == 1
    css = api.req("GET", OWNER, f"{base}/files/styles.css").text
    assert css.count("novara:brand:start") == 1 and css.count("@keyframes motion-rise") == 1
    checks = api.req("POST", OWNER, f"{base}/checks").json()
    assert [c for c in checks if c["status"] == "fail"] == []


@needs_browser
def test_an_app_with_the_motion_passes_the_browser_check(api, kit, checks_on):
    base = _apply(api, kit)
    v = api.req("POST", OWNER, f"{base}/versions", json={"message": "brand"}).json()
    assert v["checks_passed"], [c for c in v["checks"] if c["status"] != "pass"]
    assert [c for c in v["checks"] if c["check"].startswith("browser") and c["status"] != "pass"] == []


def test_the_design_agent_chooses_how_the_brand_moves(api, kit):
    kit["ai"].script = [
        tool_reply("choose_motion", {"personality": "bouncy"}, tool_use_id="m1"),
        tool_reply("choose_motion", {"personality": "crisp", "speed": 0.75, "rationale": "A law firm: precise."},
                   tool_use_id="m2"),
        text_reply("Crisp, quick motion suits a law firm."),
    ]
    out = api.req("POST", OWNER, k_url(kit, "/concepts"), json={"brief": "مكتب محاماة"}).json()
    assert kit["ai"].requests[1]["messages"][-1]["content"][0].get("is_error") is True   # not a personality
    assert (out["motion"]["personality"], out["motion"]["speed"]) == ("crisp", 0.75)
    assert api.req("GET", OWNER, k_url(kit)).json()["motion"]["tokens"]["motion-fast"] == "80ms"
