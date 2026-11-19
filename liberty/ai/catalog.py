"""The Anthropic model catalogue the UI offers — the App editor's Model dropdown and the
chat's model picker (``[ai] allowed_models``). Ordered best-first within each family. The
editor accepts any id saved on disk: an id that is not listed here still shows (as "custom")
so an experimental or legacy pin stays visible."""

from __future__ import annotations

KNOWN_MODELS: list[dict[str, str]] = [
    {"id": "claude-opus-5-5",            "label": "Opus 5.5 (recommended)"},
    {"id": "claude-sonnet-5-5",          "label": "Sonnet 5.5"},
    {"id": "claude-haiku-5-5",           "label": "Haiku 5.5 (fast, cheap)"},
    {"id": "claude-fable-5-1",           "label": "Fable 5.1 (most capable)"},
    {"id": "claude-opus-4-8",            "label": "Opus 4.8"},
    {"id": "claude-opus-4-7",            "label": "Opus 4.7"},
    {"id": "claude-opus-4-6",            "label": "Opus 4.6"},
    {"id": "claude-sonnet-4-6",          "label": "Sonnet 4.6"},
    {"id": "claude-sonnet-4-5",          "label": "Sonnet 4.5 (deprecated)"},
    {"id": "claude-haiku-4-5-20251001",  "label": "Haiku 4.5"},
]


# List prices in USD per million tokens — ``(input, output, cache read)`` — for the estimate of
# Monitoring → AI usage. As of this release; a cache write is billed 1.25 × the input price. A
# model that is not listed has no estimate (the page says so) rather than a wrong one. Matched by
# prefix, so a dated id (``claude-haiku-4-5-20251001``) finds its family.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-2": (8.0, 40.0, 0.20),
    "claude-fable-5-1": (10.0, 50.0, 1.0),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}
CACHE_WRITE_FACTOR = 1.25


def price_of(model_id: str) -> tuple[float, float, float] | None:
    """The list price of *model_id* (the longest listed id it starts with), or None."""
    best = max((k for k in PRICES if str(model_id).startswith(k)), key=len, default=None)
    return PRICES[best] if best else None


def estimate_cost(model_id: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_creation_tokens: int) -> float | None:
    """What these tokens cost at list price, in USD — None for a model without a listed price."""
    price = price_of(model_id)
    if price is None:
        return None
    p_in, p_out, p_read = price
    return (input_tokens * p_in + cache_creation_tokens * p_in * CACHE_WRITE_FACTOR + cache_read_tokens * p_read + output_tokens * p_out) / 1_000_000


def label_for(model_id: str) -> str:
    for m in KNOWN_MODELS:
        if m["id"] == model_id:
            return m["label"]
    return model_id
