"""Model routing and cost accounting.

A route names a kind of work. Every route defaults to Claude Opus 5.5 and differs only
in effort; an organization admin can point a route at another model (e.g. a cheaper
one for summaries) through the assistant settings. Prices are per million tokens, USD.
"""

from __future__ import annotations

from dataclasses import dataclass

EFFORTS = ("low", "medium", "high", "xhigh", "max")

# (input, output, cache read) USD per million tokens. Cache writes (5-minute TTL) cost 1.25x input.
MODEL_PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}


@dataclass(frozen=True)
class Route:
    model: str
    effort: str
    max_tokens: int = 16000


DEFAULT_ROUTES: dict[str, Route] = {
    "assistant": Route("claude-opus-5-5", "medium"),        # everyday questions, lookups, drafts
    "assistant_deep": Route("claude-opus-5-5", "high"),     # multi-step analysis and planning
}


class RouteError(ValueError):
    pass


def resolve(route: str, overrides: dict | None) -> Route:
    base = DEFAULT_ROUTES.get(route)
    if base is None:
        raise RouteError(f"unknown route: {route}")
    o = (overrides or {}).get(route) or {}
    return Route(model=o.get("model", base.model), effort=o.get("effort", base.effort), max_tokens=base.max_tokens)


def validate_overrides(overrides: dict) -> dict:
    clean = {}
    for route, cfg in overrides.items():
        if route not in DEFAULT_ROUTES:
            raise RouteError(f"unknown route: {route}")
        if not isinstance(cfg, dict) or set(cfg) - {"model", "effort"}:
            raise RouteError(f"route {route}: only 'model' and 'effort' can be set")
        if "model" in cfg and cfg["model"] not in MODEL_PRICES:
            raise RouteError(f"route {route}: unsupported model {cfg['model']}")
        if "effort" in cfg and cfg["effort"] not in EFFORTS:
            raise RouteError(f"route {route}: effort must be one of {EFFORTS}")
        clean[route] = dict(cfg)
    return clean


def cost_micros(model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
                cache_write_tokens: int = 0) -> int:
    """Cost in micro-USD. Unknown models are priced at the most expensive known rate."""
    inp, out, cache_read = MODEL_PRICES.get(model, max(MODEL_PRICES.values()))
    total = (input_tokens * inp + output_tokens * out + cache_read_tokens * cache_read
             + cache_write_tokens * inp * 1.25)
    return round(total)  # tokens x USD/MTok == micro-USD
