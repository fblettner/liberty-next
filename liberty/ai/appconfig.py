"""``ai.toml`` — the assistant material an application ships (bundled with the apps, merged
like the other per-app files, edited under Settings → AI)::

    [ai.nomajde]
    starters = ["Which users have no role?", "Roles with the most members"]

    [ai.nomajde.screens.f0092]
    starters = ["Users never logged in", "Who has an expired password?"]

Starter questions are the first use; playbooks (`[ai.<app>.playbooks.<id>]`) are the next.
The operator's own ``[ai] starters`` in app.toml (keys ``*`` / app / ``app/screen``) are
merged on top by :func:`merged_starters`, so a customer can override what the app ships."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScreenAi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starters: list[str] = Field(default_factory=list, description="Starter questions shown on this screen.")


class AppAi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starters: list[str] = Field(default_factory=list, description="Starter questions shown anywhere in this app.")
    screens: dict[str, ScreenAi] = Field(default_factory=dict, description="Per-screen starters, keyed by screen id.")


class AiFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai: dict[str, AppAi] = Field(default_factory=dict, description="One block per app (the connector name).")

    def starters(self) -> dict[str, list[str]]:
        """``{app: [...], "app/screen": [...]}`` — empty lists dropped."""
        out: dict[str, list[str]] = {}
        for app, block in self.ai.items():
            if block.starters:
                out[app] = list(block.starters)
            for sid, scr in block.screens.items():
                if scr.starters:
                    out[f"{app}/{sid}"] = list(scr.starters)
        return out


def parse_ai_config(data: dict[str, Any]) -> AiFile:
    return AiFile.model_validate(data)


def load_ai_config(path: Path | str) -> AiFile:
    """A missing file is fine (nothing shipped)."""
    p = Path(path)
    if not p.exists():
        return AiFile()
    return parse_ai_config(tomllib.loads(p.read_text(encoding="utf-8")))


def merged_starters(bundled: AiFile | None, operator: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """The apps' starters, then the operator's ``[ai] starters`` on top (same key → the
    operator's list wins; an empty operator list removes the key)."""
    out = bundled.starters() if bundled is not None else {}
    for key, qs in (operator or {}).items():
        if qs:
            out[key] = list(qs)
        else:
            out.pop(key, None)
    return out
