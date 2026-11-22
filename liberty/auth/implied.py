"""What a menu grant implies.

A role says what a person may open — a menu folder or one of its entries — and at which level:
**read** (``menu:<app>:<id>``) or **read and change** (``edit:<app>:<id>`` on top). The server
works out the rest: the queries, endpoints and functions the screens under that node need. So a
role stays a handful of lines whatever the size of the application, it follows its folder when
an upgrade adds a screen to it, and the sign-in token does not carry hundreds of query names.

This module holds the answer and the check; :mod:`liberty.web.menu_access` builds the answer
from the loaded configuration. Nothing here knows about screens.

The check, for a permission the caller does not hold by name:

* every menu entry that needs this permission is a candidate, at the level it needs it;
* an entry counts when its **nearest** node with a rule — itself, else its folder, else the
  folder above — allows it: ``menu:`` for read, and ``edit:`` as well for change. A deny
  (``!menu:<app>:<id>``, ``!edit:<app>:<id>``) on that nearest node stops it;
* a rule written with ``*`` (``menu:<app>:*`` — the whole application) is the outermost one: it
  only speaks when no node of the chain is named, so "the application in change, this folder
  in read" reads as it is written;
* an explicit deny of the permission itself (``!sql:…``) always wins — that is checked by
  :meth:`liberty.auth.principal.Principal.has_permission` before coming here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

READ = "read"
CHANGE = "change"
MENU = "menu"
EDIT = "edit"


def _segment_match(pattern: str, value: str) -> bool:
    from liberty.auth.principal import _segment_match as match   # principal imports this module

    return match(pattern, value)


def _rule_on(permissions: Iterable[str], key: str, *, named: bool) -> bool | None:
    """What the caller's rules say of one node key: denied (False), allowed (True), nothing (None).
    *named* = the rules that name this very node; else the rules written with ``*`` (a whole
    application, every application). The bare ``*`` is not a rule on a node — who holds it never
    comes here."""
    allowed = False
    for p in permissions:
        deny = p.startswith("!")
        core = p[1:] if deny else p
        if core == "*" or ("*" in core.split(":")) == named:
            continue
        if not (core == key if named else _segment_match(core, key)):
            continue
        if deny:
            return False
        allowed = True
    return True if allowed else None


@dataclass(slots=True)
class MenuAccess:
    """What every menu entry needs, and which entries need a given permission."""

    # (app, entry id) → the entry, then its folder, then the folder above… up to the root
    chains: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    # (app, entry id) → {"read": […], "change": […]} — "change" holds only what read does not
    needs: dict[tuple[str, str], dict[str, tuple[str, ...]]] = field(default_factory=dict)
    # permission → [(app, entry id, level)]
    needed_by: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)

    def add(self, app: str, entry: str, chain: tuple[str, ...], read: Iterable[str], change: Iterable[str]) -> None:
        r = tuple(sorted(set(read)))
        c = tuple(sorted(set(change) - set(r)))
        self.chains[(app, entry)] = chain
        self.needs[(app, entry)] = {READ: r, CHANGE: c}
        for level, perms in ((READ, r), (CHANGE, c)):
            for p in perms:
                self.needed_by.setdefault(p, []).append((app, entry, level))

    def _granted(self, permissions: tuple[str, ...], prefix: str, app: str, entry: str) -> bool:
        chain = self.chains.get((app, entry), (entry,))
        for node in chain:                              # the nearest node that is named decides
            said = _rule_on(permissions, f"{prefix}:{app}:{node}", named=True)
            if said is not None:
                return said
        # no node of the chain is named: the rules on the whole application (written with *)
        return bool(_rule_on(permissions, f"{prefix}:{app}:{chain[0]}", named=False))

    def level(self, permissions: tuple[str, ...], app: str, entry: str) -> str | None:
        """The level the rules give on one entry: None, ``read`` or ``change``."""
        if not self._granted(permissions, MENU, app, entry):
            return None
        return CHANGE if self._granted(permissions, EDIT, app, entry) else READ

    def grants(self, permissions: tuple[str, ...], permission: str) -> bool:
        for app, entry, need in self.needed_by.get(permission, ()):
            got = self.level(permissions, app, entry)
            if got == CHANGE or (got == READ and need == READ):
                return True
        return False

    def implied(self, permissions: tuple[str, ...]) -> list[str]:
        """Everything the rules give through the menus — what the browser is told, so its own
        checks (a page gating a button) agree with the server's."""
        out: set[str] = set()
        for (app, entry), need in self.needs.items():
            got = self.level(permissions, app, entry)
            if got is None:
                continue
            out.update(need[READ])
            if got == CHANGE:
                out.update(need[CHANGE])
        return sorted(out)


# The loaded application's answer, read at each check (a reload replaces the configuration, so the
# answer is asked again, never kept here). None outside a running server: nothing is implied.
_source: Callable[[], MenuAccess | None] | None = None


def set_source(source: Callable[[], MenuAccess | None] | None) -> None:
    global _source
    _source = source


def current() -> MenuAccess | None:
    if _source is None:
        return None
    try:
        return _source()
    except Exception:  # noqa: BLE001 — a configuration that cannot be read implies nothing; it is not a reason to fail a check
        return None


def _names_a_node(permissions: tuple[str, ...]) -> bool:
    return any(p.startswith(f"{MENU}:") for p in permissions)


def grants(permissions: tuple[str, ...], permission: str) -> bool:
    """Whether a menu grant among *permissions* gives *permission*. False without any ``menu:``
    rule (the common case costs one pass over the caller's rules)."""
    if not _names_a_node(permissions):
        return False
    access = current()
    return access is not None and access.grants(permissions, permission)


def implied(permissions: tuple[str, ...]) -> list[str]:
    if not _names_a_node(permissions):
        return []
    access = current()
    return access.implied(permissions) if access is not None else []
