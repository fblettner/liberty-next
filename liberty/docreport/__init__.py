"""Documentation reports (C12): the technical report (the release, read from the running code) and the
implementation report (this install, read from its configuration and state). Both are Markdown rendered
by :mod:`liberty.reports.render` — the PDF of the applications' reports — and served by
``/admin/dev/reports`` (superuser). :data:`CATALOG` lists their sections for the Reports page."""

from __future__ import annotations

import re
from typing import Any

from liberty.reports.schema import ReportContent

# kind → (title fr/en, subtitle fr/en, [(section id, title fr/en, description fr/en, default on)], options)
CATALOG: dict[str, dict[str, Any]] = {
    "implementation": {
        "title": ("Rapport d'implémentation", "Implementation report"),
        "subtitle": ("Documentation technique et fonctionnelle de l'installation", "Technical and functional documentation of the install"),
        "sections": [
            ("context", ("Contexte", "Context"), ("Version, applications, pools, licence", "Version, applications, pools, licence"), True),
            ("architecture", ("Architecture", "Architecture"), ("Composants actifs : comptes, OIDC, IA, tâches, notifications", "Active components: accounts, OIDC, AI, jobs, notifications"), True),
            ("applications", ("Applications et menus", "Applications and menus"), ("Les entrées de menu de chaque application et leurs rôles", "Each application's menu entries and their roles"), True),
            ("screens", ("Écrans", "Screens"), ("Requêtes, écritures, colonnes, onglets et actions par écran", "Queries, writes, columns, tabs and actions per screen"), True),
            ("connectors", ("Connecteurs et pools", "Connectors and pools"), ("Pools, connecteurs SQL et API, état de chargement", "Pools, SQL and API connectors, load state"), True),
            ("analytics", ("Tableaux de bord et Explorateur", "Dashboards and Explorer"), ("Tableaux de bord, graphiques, modèles d'analyse", "Dashboards, charts, analysis models"), True),
            ("operations", ("Exploitation", "Operations"), ("Tâches planifiées, règles de notification, actions partagées", "Scheduled jobs, notification rules, shared actions"), True),
            ("security", ("Sécurité", "Security"), ("Jetons, rôles, utilisateurs", "Tokens, roles, users"), True),
            ("reference", ("Référentiels", "Reference data"), ("Dictionnaire : listes de valeurs, recherches, séquences", "Dictionary: enums, lookups, sequences"), True),
            ("appendixConfig", ("Annexe : réglages", "Appendix: settings"), ("Toutes les valeurs de app.toml, secrets masqués", "Every app.toml value, secrets masked"), False),
            ("appendixApi", ("Annexe : référence de l'API", "Appendix: API reference"), ("Chaque route avec ses paramètres", "Every route with its parameters"), False),
        ],
        "options": [],
    },
    "technical": {
        "title": ("Rapport technique", "Technical report"),
        "subtitle": ("Documentation développeur : modèle de données, configuration, API", "Developer documentation: data model, configuration, API"),
        "sections": [
            ("scope", ("Périmètre", "Scope"), ("Version, date et sources du rapport", "Version, date and sources of the report"), True),
            ("architecture", ("Architecture", "Architecture"), ("Composants, exécution, sécurité", "Components, runtime, security"), True),
            ("data", ("Modèle de données", "Data model"), ("Tables du framework et des connecteurs, colonnes et usages", "Framework and connector tables, columns and uses"), True),
            ("configuration", ("Référence de configuration", "Configuration reference"), ("Chaque clé de app.toml, son écran, ses lecteurs, les écarts", "Every app.toml key, its screen, its readers, the gaps"), True),
            ("api", ("Référence de l'API", "API reference"), ("Chaque route avec l'accès qu'elle exige", "Every route with the access it requires"), True),
            ("cli", ("Ligne de commande", "Command line"), ("Les commandes installées et leurs sous-commandes", "The installed commands and their sub-commands"), True),
            ("build", ("Compilation et paquetage", "Build and packaging"), ("Étapes, paquet, dépendances, contenu", "Steps, package, dependencies, content"), True),
            ("appendix", ("Annexe : références dans le code", "Appendix: code references"), ("Les fichiers qui lisent chaque clé de configuration", "The files reading each configuration key"), True),
        ],
        "options": [("api_detail", "api", ("Détailler chaque route de l'API (description et paramètres)", "Detail every API route (description and parameters)")),
                    ("snippets", "appendix", ("Inclure les extraits de code en annexe (long)", "Include the code snippets in the appendix (long)"))],
    },
}


def catalog(lang: str) -> list[dict[str, Any]]:
    i = 1 if lang == "en" else 0
    return [{
        "kind": kind, "title": c["title"][i], "subtitle": c["subtitle"][i],
        "sections": [{"id": s, "title": ti[i], "description": de[i], "default": on} for s, ti, de, on in c["sections"]],
        "options": [{"id": o, "section": sec, "label": lb[i]} for o, sec, lb in c["options"]],
    } for kind, c in CATALOG.items()]


def _renumber(markdown: str) -> str:
    """Number the ``##`` sections 1..n in the order they appear and their ``###`` sub-sections after them."""
    out, n = [], 0
    for line in markdown.splitlines():
        if re.match(r"^## \d+\. ", line):
            n += 1
            line = re.sub(r"^## \d+\. ", f"## {n}. ", line)
        elif re.match(r"^### \d+\.\d+ ", line):
            line = re.sub(r"^### \d+\.", f"### {n}.", line)
        out.append(line)
    return "\n".join(out) + "\n"


async def build(kind: str, app: Any, *, lang: str, sections: list[str] | None, author: str,
                date_label: str | None = None, options: dict[str, bool] | None = None) -> ReportContent:
    from liberty.docreport import implementation, technical
    spec = CATALOG[kind]
    wanted = sections or [s for s, _t, _d, on in spec["sections"] if on]
    state = app.state
    settings = getattr(state, "settings", None)
    ctx: dict[str, Any] = {"app": app, "state": state, "settings": settings, "author": author, **(options or {}),
                           "frontend_dir": getattr(state, "frontend_dir", None)}
    if date_label:
        ctx["date"] = date_label
    if settings is not None:
        from pathlib import Path
        ctx["config_dir"] = Path(settings.connectors.config_path).parent
    if kind == "technical":
        import asyncio
        body = await asyncio.to_thread(technical.render, wanted, lang, ctx)
    else:
        body = await implementation.render(wanted, lang, ctx)
    i = 1 if lang == "en" else 0
    name = getattr(getattr(settings, "app", None), "name", "") or "Liberty"
    from liberty import __version__
    return ReportContent(
        markdown=_renumber(body),
        title=f"{spec['title'][i]} {name}",
        filename_base=f"liberty-{kind}-{ctx.get('date') or ''}".rstrip("-").replace(" ", "_"),
        pdf_options={"subtitle": spec["subtitle"][i], "cover_eyebrow": spec["title"][i], "lang": "en" if lang == "en" else "fr",
                     "version": __version__, "author": author, **({"date": ctx["date"]} if ctx.get("date") else {})},
    )
