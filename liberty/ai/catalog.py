"""The Anthropic model catalogue the UI offers — the App editor's Model dropdown and the
chat's model picker (``[ai] allowed_models``). Ordered best-first within each family. The
editor accepts any id saved on disk: an id that is not listed here still shows (as "custom")
so an experimental or legacy pin stays visible."""

from __future__ import annotations

KNOWN_MODELS: list[dict[str, str]] = [
    {"id": "claude-opus-5-5",            "label": "Opus 5.5 (latest)"},
    {"id": "claude-sonnet-5-5",          "label": "Sonnet 5.5"},
    {"id": "claude-haiku-5-5",           "label": "Haiku 5.5 (fast, cheap)"},
    {"id": "claude-fable-5-1",           "label": "Fable 5.1 (most capable)"},
    {"id": "claude-opus-4-8",            "label": "Opus 4.8 (recommended)"},
    {"id": "claude-opus-4-7",            "label": "Opus 4.7"},
    {"id": "claude-opus-4-6",            "label": "Opus 4.6"},
    {"id": "claude-sonnet-4-6",          "label": "Sonnet 4.6"},
    {"id": "claude-sonnet-4-5",          "label": "Sonnet 4.5 (deprecated)"},
    {"id": "claude-haiku-4-5-20251001",  "label": "Haiku 4.5"},
]


def label_for(model_id: str) -> str:
    for m in KNOWN_MODELS:
        if m["id"] == model_id:
            return m["label"]
    return model_id
