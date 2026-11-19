"""The Claude Desktop extension (``liberty.mcpb``) — the MCP endpoint for a server that is not
on the internet.

A custom connector in Claude is called from Anthropic's servers: it needs a public address (and
is where the OAuth sign-in of :mod:`liberty.auth.oauth` applies). An extension runs on the
user's machine instead: Claude Desktop starts the small bridge of ``mcpb/server/index.js``,
which posts each message to ``/mcp`` with the user's personal access token. The server then only
has to be reachable from that machine — an internal address, a VPN.

The file is a zip built at download: the manifest with this server's address and version filled
in, the bridge (Node's own modules, nothing to install) and the icon. The user opens it, pastes
a token created in their profile, and that is all.

Claude Desktop tells extensions apart by their manifest ``name``: with one fixed name, the
extension of a second Liberty server would replace the first. So the name carries the instance —
its host and its environment (``[app] environment``) — and the extensions of several servers
(production, test, another customer) install side by side, each shown under its own label.
"""

from __future__ import annotations

import io
import re
import zipfile
from importlib import resources

FILE_NAME = "liberty.mcpb"
_ADDRESS = re.compile(r"https?://[^\s\"\\]+")


def instance_names(address: str | None, environment: str | None) -> tuple[str, str, str]:
    """``(name, display name, file name)`` of the extension of the instance at *address* in
    *environment*: ``liberty-next-<host>-<environment>``, "Liberty Next – <environment> · <host>",
    ``liberty-<host>-<environment>.mcpb`` — the plain names when neither is known."""
    from urllib.parse import urlsplit

    host = ""
    url = (address or "").strip()
    if _ADDRESS.fullmatch(url):
        parts = urlsplit(url)
        host = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
    env = re.sub(r'[\\"\x00-\x1f\x7f]', "", environment or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", f"{host}{'-' + env if env else ''}".lower()).strip("-")
    label = " · ".join(x for x in (env, host) if x)
    return (
        f"liberty-next-{slug}" if slug else "liberty-next",
        f"Liberty Next – {label}" if label else "Liberty Next",
        f"liberty-{slug}.mcpb" if slug else FILE_NAME,
    )


def _resource(name: str) -> bytes:
    return resources.files("liberty.ai").joinpath("mcpb", *name.split("/")).read_bytes()


def _version(release: str | None) -> str:
    """*release* as the ``X.Y.Z`` an extension manifest wants (a development build has none)."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", release or "")
    return ".".join(str(int(g)) for g in m.groups()) if m else "1.0.0"


# The extension's own texts (its description and settings) in French, by their English wording
# in the manifest — what Claude Desktop shows; the tools' texts come from the server.
FRENCH: tuple[tuple[str, str], ...] = (
    ("Use your Liberty Next applications from Claude: screens, dashboards, Data Explorer analyses and playbooks, under your own account.",
     "Utilisez vos applications Liberty Next depuis Claude : écrans, tableaux de bord, analyses du Data Explorer et procédures, sous votre propre compte."),
    ("Liberty address", "Adresse de Liberty"),
    ("The address of your Liberty server, ending with /mcp.", "L'adresse de votre serveur Liberty, terminée par /mcp."),
    ("Personal access token", "Jeton d'accès personnel"),
    ("Created in Liberty: your profile, tab Access tokens.", "Créé dans Liberty : votre profil, onglet Jetons d'accès."),
    ("Files folder", "Dossier de fichiers"),
    ("Optional. Claude can send Liberty the spreadsheets and delimited files of this folder, and of no other, for a job that loads a file, when you ask it to.",
     "Facultatif. Claude peut envoyer à Liberty les classeurs et les fichiers délimités de ce dossier, et d'aucun autre, pour une tâche qui charge un fichier, lorsque vous le lui demandez."),
    ("Accept an internal certificate", "Accepter un certificat interne"),
    ("Turn on only when your Liberty server uses a self-signed or company certificate.",
     "À activer uniquement si votre serveur Liberty utilise un certificat auto-signé ou d'entreprise."),
)


def build_extension(address: str | None, release: str | None, environment: str | None = None, language: str | None = None) -> bytes:
    """The extension for this server: *address* is where the user's machine reaches ``/mcp``
    (pre-filled in the extension's settings; anything that is not an http(s) address is left
    blank for the user to type); *environment* names the instance with the address's host;
    *language* is the user's (``fr…`` → the extension's settings in French)."""
    import json

    url = (address or "").strip()
    if not _ADDRESS.fullmatch(url):
        url = ""
    name, display, _file = instance_names(url, environment)
    manifest = (
        _resource("manifest.json").decode("utf-8")
        .replace("{{version}}", _version(release)).replace("{{url}}", url)
        .replace("{{name}}", name).replace("{{display_name}}", json.dumps(display, ensure_ascii=False)[1:-1])
    )
    if (language or "").lower().startswith("fr"):
        for english, french in FRENCH:
            manifest = manifest.replace(json.dumps(english), json.dumps(french, ensure_ascii=False))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("server/index.js", _resource("server/index.js"))
        zf.writestr("icon.png", _resource("icon.png"))
    return out.getvalue()
