"""How a brand moves: motion tokens, ready-made animations and a scroll reveal, generated from a personality.

Everything that moves is inside ``prefers-reduced-motion: no-preference``; people who ask their system for less
motion get the end state at once (WCAG 2.3.3). Nothing loops forever or flashes (WCAG 2.3.1): the attention pulse
runs twice, slower than once a second. Slides follow the reading direction, so they mirror in Arabic.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_MS, MAX_MS = 80, 500          # shorter isn't seen as motion; longer makes the interface feel slow


@dataclass(frozen=True)
class Personality:
    name: str
    fast: int                      # hovers, presses, colour changes
    base: int                      # small elements appearing
    slow: int                      # sections and cards entering
    ease: str
    ease_enter: str
    ease_exit: str
    distance: int                  # px an element travels as it enters
    stagger: int                   # ms between items of a list
    lift: int                      # px a card rises on hover
    press: float                   # scale while a button is held


PERSONALITIES: dict[str, Personality] = {
    "calm": Personality("calm", 160, 260, 420, "cubic-bezier(0.4, 0, 0.2, 1)", "cubic-bezier(0, 0, 0.2, 1)",
                        "cubic-bezier(0.4, 0, 1, 1)", 8, 70, 2, 0.98),
    "lively": Personality("lively", 140, 240, 380, "cubic-bezier(0.4, 0, 0.2, 1)", "cubic-bezier(0.34, 1.36, 0.64, 1)",
                          "cubic-bezier(0.4, 0, 1, 1)", 16, 50, 4, 0.96),
    "crisp": Personality("crisp", 100, 160, 240, "cubic-bezier(0.2, 0, 0, 1)", "cubic-bezier(0.2, 0, 0, 1)",
                         "cubic-bezier(0.3, 0, 1, 1)", 6, 30, 1, 0.98),
    "still": Personality("still", 120, 120, 160, "linear", "linear", "linear", 0, 0, 0, 1.0),
}
DEFAULT = {"personality": "calm", "speed": 1.0}


class MotionError(ValueError):
    pass


def build(personality: str = "calm", speed: float = 1.0) -> dict:
    """Tokens and notes for a personality at a speed (0.75 = a quarter faster, 1.5 = half again slower)."""
    if personality not in PERSONALITIES:
        raise MotionError(f"unknown motion personality: {personality}")
    if not 0.5 <= speed <= 2:
        raise MotionError("speed must be between 0.5 and 2")
    p = PERSONALITIES[personality]
    notes: list[str] = []
    durations = {}
    for role in ("fast", "base", "slow"):
        ms = round(getattr(p, role) * speed)
        if ms > MAX_MS:
            notes.append(f"{role} {ms}ms shortened to {MAX_MS}ms: slower movement makes the interface feel sluggish")
            ms = MAX_MS
        elif ms < MIN_MS:
            notes.append(f"{role} {ms}ms lengthened to {MIN_MS}ms: shorter movement isn't seen")
            ms = MIN_MS
        durations[role] = ms
    tokens = {
        "motion-fast": f"{durations['fast']}ms", "motion-base": f"{durations['base']}ms",
        "motion-slow": f"{durations['slow']}ms", "motion-ease": p.ease, "motion-ease-enter": p.ease_enter,
        "motion-ease-exit": p.ease_exit, "motion-distance": f"{p.distance}px", "motion-stagger": f"{round(p.stagger * speed)}ms",
        "motion-lift": f"{p.lift}px", "motion-press": f"{p.press}",
    }
    return {"personality": personality, "speed": speed, "tokens": tokens, "notes": notes}


def to_css(motion: dict) -> str:
    tokens = "\n".join(f"  --{k}: {v};" for k, v in motion["tokens"].items())
    return f"""/* Motion: {motion['personality']}. Everything that moves respects prefers-reduced-motion. */
:root {{
{tokens}
  --motion-dir: 1;
}}
[dir="rtl"] {{ --motion-dir: -1; }}

@keyframes motion-fade {{ from {{ opacity: 0; }} }}
@keyframes motion-rise {{ from {{ opacity: 0; transform: translateY(var(--motion-distance)); }} }}
@keyframes motion-slide {{ from {{ opacity: 0; transform: translateX(calc(var(--motion-distance) * -2 * var(--motion-dir))); }} }}
@keyframes motion-zoom {{ from {{ opacity: 0; transform: scale(0.96); }} }}
@keyframes motion-pulse {{ 50% {{ transform: scale(1.06); }} }}

@media (prefers-reduced-motion: no-preference) {{
  .motion-fade, .motion-rise, .motion-slide, .motion-zoom {{
    animation-duration: var(--motion-slow);
    animation-timing-function: var(--motion-ease-enter);
    animation-fill-mode: both;
    animation-delay: calc(var(--motion-order, 0) * var(--motion-stagger));
  }}
  .motion-fade {{ animation-name: motion-fade; }}
  .motion-rise {{ animation-name: motion-rise; }}
  .motion-slide {{ animation-name: motion-slide; }}
  .motion-zoom {{ animation-name: motion-zoom; }}
  .motion-ready [data-reveal] {{
    transition: opacity var(--motion-slow) var(--motion-ease-enter), transform var(--motion-slow) var(--motion-ease-enter);
    transition-delay: calc(var(--motion-order, 0) * var(--motion-stagger));
  }}
  .motion-ready [data-reveal]:not(.is-revealed) {{ opacity: 0; transform: translateY(var(--motion-distance)); }}
  .motion-lift {{ transition: transform var(--motion-fast) var(--motion-ease), box-shadow var(--motion-fast) var(--motion-ease); }}
  .motion-lift:hover {{ transform: translateY(calc(var(--motion-lift) * -1)); box-shadow: 0 8px 24px rgb(0 0 0 / 0.12); }}
  .motion-press {{ transition: transform var(--motion-fast) var(--motion-ease); }}
  .motion-press:active {{ transform: scale(var(--motion-press)); }}
  .motion-pulse {{ animation: motion-pulse 1.2s var(--motion-ease) 2; }}
  a, button, input, select, textarea {{
    transition: color var(--motion-fast) var(--motion-ease), background-color var(--motion-fast) var(--motion-ease),
                border-color var(--motion-fast) var(--motion-ease), box-shadow var(--motion-fast) var(--motion-ease);
  }}
}}

@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    animation-delay: 0ms !important;
    transition-duration: 1ms !important;
    transition-delay: 0ms !important;
    scroll-behavior: auto !important;
  }}
}}
"""


SCRIPT = """/* NOVARA motion: reveals [data-reveal] elements as they scroll into view.
 * Without JavaScript, or for people who ask for less motion, everything is simply visible. */
(function () {
  if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.documentElement.classList.add("motion-ready");
  var seen = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-revealed");
      seen.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -8% 0px" });
  function start() {
    document.querySelectorAll("[data-reveal]").forEach(function (el, i) {
      if (!el.style.getPropertyValue("--motion-order")) el.style.setProperty("--motion-order", String(i % 6));
      seen.observe(el);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
"""

USAGE = [
    ("motion-rise", "enters by rising a little: sections, cards, headings"),
    ("motion-slide", "enters from the side the text starts on (right in Arabic)"),
    ("motion-fade / motion-zoom", "enters by fading, or growing slightly"),
    ("data-reveal", "the same rise, when the element scrolls into view (needs motion.js)"),
    ("--motion-order", "position in a list (0, 1, 2...): items enter one after another"),
    ("motion-lift", "cards that rise on hover"),
    ("motion-press", "buttons that give a little when pressed"),
    ("motion-pulse", "draws attention twice, then stops: a new badge, a saved state"),
]
