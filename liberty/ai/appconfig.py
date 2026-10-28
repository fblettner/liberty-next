"""``ai.toml`` — the assistant material an application ships (bundled with the apps, merged
like the other per-app files, edited under Settings → AI)::

    [ai.nomajde]
    starters = ["Which users have no role?", "Roles with the most members"]

    [ai.nomajde.screens.f0092]
    starters = ["Users never logged in", "Who has an expired password?"]

    [ai.nomajde.playbooks.create_user]
    title = "Create a JDE user"
    description = "User, role, environments"
    body = "1. Open Security → Users and click Add…\n2. …"   # Markdown (a multi-line TOML string)

Playbooks are step-by-step procedures (Markdown) the assistant loads with ``load_playbook``
when the user asks how to do something in the application.
A block keyed ``"*"`` (``[ai."*"]``) is for every application: its starters apply where no
app block does, its playbooks are listed under the app ``*``.
The operator's own ``[ai] starters`` in app.toml (keys ``*`` / app / ``app/screen``) are
merged on top by :func:`merged_starters`, so a customer can override what the app ships."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StarterGroup(BaseModel):
    """Starter questions under a heading: the chat shows the headings as pills and the picked
    heading's questions as chips (nomaubl's domains)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    starters: list[str] = Field(default_factory=list)


class ScreenAi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starters: list[str] = Field(default_factory=list, description="Starter questions shown on this screen.")
    groups: list[StarterGroup] = Field(default_factory=list, description="Starter questions by heading; shown instead of `starters` when set.")


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, description="What the procedure achieves — the assistant picks a playbook by it.")
    description: str | None = Field(default=None, description="One line on when it applies.")
    body: str = Field(min_length=1, description="The procedure, Markdown; the assistant follows it step by step.")


class AiLocale(BaseModel):
    """The starters of one app in one language: what ``l.fr`` carries — the app's starters and
    groups, and the screens' — each list replacing its English counterpart when present."""

    model_config = ConfigDict(extra="forbid")

    starters: list[str] = Field(default_factory=list)
    groups: list[StarterGroup] = Field(default_factory=list)
    screens: dict[str, ScreenAi] = Field(default_factory=dict)


class AppAi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starters: list[str] = Field(default_factory=list, description="Starter questions shown anywhere in this app.")
    groups: list[StarterGroup] = Field(default_factory=list, description="Starter questions by heading; shown instead of `starters` when set.")
    screens: dict[str, ScreenAi] = Field(default_factory=dict, description="Per-screen starters, keyed by screen id.")
    playbooks: dict[str, Playbook] = Field(default_factory=dict, description="Procedures the assistant can load, keyed by id.")
    l: dict[str, AiLocale] = Field(default_factory=dict, description="The starters per language (``l.fr``): a list given there replaces the English one for that language.")

    def for_language(self, language: str | None) -> AppAi:
        """This block with the lists of *language* (``fr``, or ``fr-FR`` → ``fr``) swapped in."""
        if not language:
            return self
        lang = language.lower()
        loc = self.l.get(lang) or self.l.get(lang.split("-")[0])
        if loc is None:
            return self
        screens = dict(self.screens)
        for sid, scr in loc.screens.items():
            base = screens.get(sid, ScreenAi())
            screens[sid] = ScreenAi(starters=scr.starters or base.starters, groups=scr.groups or base.groups)
        return self.model_copy(update={"starters": loc.starters or self.starters, "groups": loc.groups or self.groups, "screens": screens})


class AiFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai: dict[str, AppAi] = Field(default_factory=dict, description="One block per app (the connector name).")

    def starters(self, language: str | None = None) -> dict[str, list[str]]:
        """``{app: [...], "app/screen": [...]}`` — empty lists dropped; in *language* when the
        app ships it."""
        out: dict[str, list[str]] = {}
        for app, block in self.ai.items():
            block = block.for_language(language)
            if block.starters:
                out[app] = list(block.starters)
            for sid, scr in block.screens.items():
                if scr.starters:
                    out[f"{app}/{sid}"] = list(scr.starters)
        return out

    def starter_groups(self, language: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """``{app | "app/screen": [{label, starters}, …]}`` — the grouped starters only, in
        *language* when the app ships it."""
        out: dict[str, list[dict[str, Any]]] = {}
        for app, block in self.ai.items():
            block = block.for_language(language)
            if block.groups:
                out[app] = [g.model_dump() for g in block.groups]
            for sid, scr in block.screens.items():
                if scr.groups:
                    out[f"{app}/{sid}"] = [g.model_dump() for g in scr.groups]
        return out

    def playbooks(self) -> list[tuple[str, str, Playbook]]:
        """``(app, id, playbook)`` for every playbook, apps and ids in file order."""
        return [(app, pid, pb) for app, block in self.ai.items() for pid, pb in block.playbooks.items()]


def parse_ai_config(data: dict[str, Any]) -> AiFile:
    return AiFile.model_validate(data)


def load_ai_config(path: Path | str) -> AiFile:
    """A missing file is fine (nothing shipped)."""
    p = Path(path)
    if not p.exists():
        return AiFile()
    return parse_ai_config(tomllib.loads(p.read_text(encoding="utf-8")))


def merged_starters(bundled: AiFile | None, operator: dict[str, list[str]] | None, language: str | None = None) -> dict[str, list[str]]:
    """The apps' starters, then the operator's ``[ai] starters`` on top (same key → the
    operator's list wins; an empty operator list removes the key)."""
    out = bundled.starters(language) if bundled is not None else {}
    for key, qs in (operator or {}).items():
        if qs:
            out[key] = list(qs)
        else:
            out.pop(key, None)
    return out
