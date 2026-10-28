"""The technical report (C12): developer documentation of the release, read from the running code —
scope, architecture, data model, configuration reference, API, command line, packaging and the code
references of each setting. Identical on every install of a version, but for the connectors' tables,
which come from the install's configuration."""

from __future__ import annotations

import importlib
import importlib.metadata as md
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from liberty.devref import api_reference, config_reference, data_model
from liberty.docreport.md import Lang, aside, code, first_sentence, table

_ROOT = Path(__file__).resolve().parent.parent


def _version(dist: str) -> str:
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return ""


def scope(t: Lang, ctx: dict[str, Any]) -> str:
    rows = [
        (t("Version Liberty Next", "Liberty Next version"), _version("liberty-next") or "—"),
        (t("Applications (liberty-apps)", "Applications (liberty-apps)"), _version("liberty-apps") or t("non installées", "not installed")),
        (t("Généré le", "Generated on"), f"{ctx['date']} {t('par', 'by')} {ctx['author']}"),
        (t("Sources", "Sources"), t(
            "le code en cours d'exécution : modèles des tables du framework, connecteurs et écrans de l'installation, "
            "modèle des réglages (app.toml) et code qui les lit, routes de l'API et leur schéma OpenAPI, lignes de commande, métadonnées du paquet.",
            "the running code: the framework's table models, the install's connectors and screens, the settings model "
            "(app.toml) and the code reading it, the API routes and their OpenAPI schema, the command lines, the package metadata.")),
    ]
    return (f"## 1. {t('Périmètre', 'Scope')}\n\n" + table((t("Élément", "Item"), t("Valeur", "Value")), rows)
            + "\n" + aside(t(
                "**Lecture :** ce rapport décrit le produit tel qu'il est livré ; il est identique sur toutes les installations d'une "
                "même version, à l'exception des tables des connecteurs, lues dans la configuration. Le fonctionnement et les réglages "
                "de l'installation sont dans le rapport d'implémentation.",
                "**Reading:** this report describes the product as shipped; it is the same on every install of a version, but for "
                "the connectors' tables, read from the configuration. How this install is set up and runs is in the implementation report.")))


def architecture(t: Lang, ctx: dict[str, Any]) -> str:
    comp = [
        t("**Serveur** : Python 3.12, FastAPI, SQLAlchemy 2 asynchrone ; il sert l'API, l'interface compilée et Socket.IO (verrous d'enregistrements, tableau de bord technique, journal en direct).",
          "**Server**: Python 3.12, FastAPI, async SQLAlchemy 2; it serves the API, the built interface and Socket.IO (record locks, technical dashboard, live log)."),
        t("**Interface** : React 19 + Vite + TypeScript ; elle ne parle qu'à l'API.", "**Interface**: React 19 + Vite + TypeScript; it only talks to the API."),
        t("**Connecteurs** : SQL (PostgreSQL via asyncpg, Oracle via oracledb, mode thick en option) et API HTTP ; un connecteur ne porte que des requêtes, l'écran porte l'affichage.",
          "**Connectors**: SQL (PostgreSQL through asyncpg, Oracle through oracledb, thick mode optional) and HTTP APIs; a connector holds queries only, the screen holds the display."),
        t("**nomaflow** : tâches planifiées (APScheduler), étapes SQL, copie, HTTP, Python, LDAP, rapports, chargement de fichiers, notifications.",
          "**nomaflow**: scheduled jobs (APScheduler), SQL, copy, HTTP, Python, LDAP, report, file-load and notification steps."),
        t("**Explorateur** : modèles d'analyse sur les connecteurs, une requête GROUP BY par demande, champs calculés compilés en SQL.",
          "**Explorer**: analysis models over the connectors, one GROUP BY per request, calculated fields compiled to SQL."),
        t("**Rapports** : Markdown → PDF (WeasyPrint), communs aux rapports des applications, de l'Explorateur et à ce rapport.",
          "**Reports**: Markdown → PDF (WeasyPrint), shared by the applications' reports, the Explorer and this report."),
        t("**Assistant** : SDK Anthropic, outils en lecture sur l'instance et propositions de configuration.", "**Assistant**: Anthropic SDK, read-only tools on the instance and configuration proposals."),
    ]
    run = [
        t("**Configuration** : des fichiers TOML (app, connecteurs, dictionnaire, écrans, menus, graphiques, tableaux de bord, actions, Explorateur, tâches, notifications, IA, thème) validés par des modèles Pydantic, édités par les écrans de Paramètres, versionnés avant chaque écriture, rechargés à chaud.",
          "**Configuration**: TOML files (app, connectors, dictionary, screens, menus, charts, dashboards, actions, Explorer, jobs, notifications, AI, theme) validated by Pydantic models, edited by the Settings screens, versioned before each write, hot-reloaded."),
        t("**Pas de métadonnées en base** : le schéma d'un écran vient du résultat de sa requête ; les tables du framework (comptes, exécutions, paquets, vues, notifications) sont créées et complétées au démarrage.",
          "**No metadata tables**: a screen's schema comes from its query's result; the framework's own tables (accounts, runs, packages, views, notifications) are created and completed at start-up."),
        t("**Rechargement** : une seule voie (runtime.rebuild_runtime) reconstruit les pools et rebranche chaque consommateur.", "**Reload**: a single path (runtime.rebuild_runtime) rebuilds the pools and rebinds every holder."),
        t("**Processus unique** : le planificateur et les verrous Socket.IO sont en mémoire ; un seul réplica.", "**Single process**: the scheduler and the Socket.IO locks are in memory; one replica."),
    ]
    sec = [
        t("**Authentification** : comptes locaux (argon2) ou OIDC ; jetons JWT HS256, accès 1 h, rafraîchissement 14 j avec rotation ; limitation des échecs de connexion.",
          "**Authentication**: local accounts (argon2) or OIDC; HS256 JWT, 1 h access, 14 d refresh with rotation; failed sign-in throttling."),
        t("**Droits** : permissions par segments avec jokers (sql:connecteur:requête, api:…, plugin:…, explorer:…, ai:chat) vérifiées avant toute recherche de connecteur.",
          "**Rights**: colon-segment permissions with globs (sql:connector:query, api:…, plugin:…, explorer:…, ai:chat) checked before any connector lookup."),
        t("**Périmètre des données** : règles de rôle résolues à la connexion et appliquées par le connecteur SQL à chaque lecture et écriture.",
          "**Data scope**: role rules resolved at sign-in and applied by the SQL connector to every read and write."),
        t("**Secrets** : chiffrés AES-256-GCM (préfixe ENC:) ou lus de l'environnement ; jamais exportés, masqués dans les rapports.",
          "**Secrets**: AES-256-GCM encrypted (ENC: prefix) or read from the environment; never exported, masked in reports."),
    ]
    out = [f"## 2. {t('Architecture', 'Architecture')}\n"]
    for n, (title, items) in enumerate(((t("Composants", "Components"), comp), (t("Exécution", "Runtime"), run), (t("Sécurité", "Security"), sec)), 1):
        out.append(f"### 2.{n} {title}\n\n" + "\n".join(f"- {i}" for i in items) + "\n")
    out.append(aside(t("Le fonctionnement vu de l'installation (applications, écrans, tâches, rôles) est décrit dans le rapport d'implémentation.",
                       "How this install is put together (applications, screens, jobs, roles) is in the implementation report.")))
    return "\n".join(out)


def data(t: Lang, ctx: dict[str, Any]) -> str:
    tables = data_model(ctx["state"])
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tb in tables:
        groups[(t("Framework", "Framework") + " · " + tb["group"]) if tb["owner"] == "framework" else tb["group"]].append(tb)
    out = [f"## 3. {t('Modèle de données', 'Data model')}\n",
           t("Les tables du framework (depuis leurs modèles) puis les tables déclarées par chaque connecteur : la table physique lue, la clé, "
             "les requêtes, ce qui l'utilise et les colonnes avec les libellés du dictionnaire.",
             "The framework's tables (from their models), then each connector's declared tables: the physical table read, the key, the "
             "queries, what uses it and the columns with the dictionary's labels.") + "\n",
           f"### 3.1 {t('Vue d’ensemble', 'Overview')}\n",
           table((t("Table", "Table"), t("Groupe", "Group"), t("Libellé", "Label"), t("Table physique", "Physical table"),
                  t("Colonnes", "Columns"), t("Utilisée par", "Used by")),
                 [(tb["name"], g, tb["label"] or first_sentence(tb["description"], 60), tb["physical"], len(tb["columns"]), len(tb["used_by"]))
                  for g, items in groups.items() for tb in items], align_right=(4, 5))]
    for n, (g, items) in enumerate(groups.items(), 2):
        out.append(f"### 3.{n} {g}\n")
        for tb in items:
            out.append(f"#### {tb['name']}" + (f" — {tb['label']}" if tb["label"] else "") + "\n")
            props = [(t("Rôle", "Role"), tb["description"]), (t("Table physique", "Physical table"), tb["physical"]),
                     (t("Clé", "Key"), " + ".join(tb["key"])), (t("Requêtes", "Queries"), tb["slots"]),
                     (t("Utilisée par", "Used by"), [f"{u['kind']} · {u['name']}" for u in tb["used_by"]]),
                     (t("Tables liées", "Related tables"), tb["related"])]
            out.append(table((t("Propriété", "Property"), t("Valeur", "Value")), [p for p in props if p[1]]))
            out.append(table((t("Colonne", "Column"), t("Type", "Type"), t("Clé", "Key"), t("Libellé", "Label"),
                              t("Références", "References"), t("Écrans", "Screens")),
                             [(c["name"], c["type"], "PK" if c["key"] else "", c["label"] or first_sentence(c["description"], 60),
                               c["references"], c["screens"]) for c in tb["columns"]]) or t("_Colonnes non déclarées._", "_No declared columns._") + "\n")
    return "\n".join(out)


def configuration(t: Lang, ctx: dict[str, Any]) -> str:
    keys = config_reference()
    by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for k in keys:
        by_section[k["section"]].append(k)

    def state(k: dict[str, Any]) -> str:
        return k["editor"] or (t("fichier / environnement", "file / environment") if k["file_only"] else t("absent de l'interface", "not in the UI"))

    out = [f"## 4. {t('Référence de configuration', 'Configuration reference')}\n",
           t("Chaque clé de app.toml : son type, sa valeur par défaut, l'écran qui la modifie (ou pourquoi elle reste dans le fichier) "
             "et le nombre de points du code qui la lisent. Une clé « absente de l'interface » est un écart à combler.",
             "Every app.toml key: its type, its default, the screen that edits it (or why it stays in the file) and how many places "
             "in the code read it. A key \"not in the UI\" is a gap to close.") + "\n",
           f"### 4.1 {t('Synthèse par section', 'Summary per section')}\n",
           table((t("Section", "Section"), t("Clés", "Keys"), t("Dans un écran", "In a screen"), t("Fichier / env.", "File / env."), t("Écarts", "Gaps")),
                 [(f"[{s}]", len(ks), sum(1 for k in ks if k["editor"]), sum(1 for k in ks if not k["editor"] and k["file_only"]),
                   sum(1 for k in ks if not k["editor"] and not k["file_only"])) for s, ks in by_section.items()], align_right=(1, 2, 3, 4))]
    for n, (s, ks) in enumerate(by_section.items(), 2):
        out.append(f"### 4.{n} [{s}]\n")
        out.append(table((t("Clé", "Key"), t("Type", "Type"), t("Défaut", "Default"), t("Édité dans", "Edited in"), t("Lecteurs", "Readers"), t("Description", "Description")),
                         [(code(k["key"]), k["type"], "" if k["default"] in (None, "") else str(k["default"])[:40], state(k), len(k["readers"]),
                           first_sentence(k["description"], 110)) for k in ks], align_right=(4,)))
    return "\n".join(out)


def api(t: Lang, ctx: dict[str, Any]) -> str:
    routes = api_reference(ctx["app"])
    cats: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in routes:
        cats[r["category"]][r["group"]].append(r)

    def access(r: dict[str, Any]) -> str:
        if r["access"] == "permission" and r["permissions"]:
            return " · ".join(r["permissions"])
        return {"public": t("public", "public"), "signed-in": t("utilisateur connecté", "signed-in user"),
                "superuser": t("superutilisateur", "superuser")}.get(r["access"], r["access"])

    out = [f"## 5. {t('Référence de l’API', 'API reference')}\n",
           t(f"Les {len(routes)} routes du serveur, lues dans le code : méthode, chemin, résumé et accès requis (dépendances de la route et "
             "contrôles du gestionnaire). Le schéma complet est servi sur /docs et /redoc.",
             f"The server's {len(routes)} routes, read from the code: method, path, summary and the access required (the route's "
             "dependencies and the handler's checks). The full schema is served on /docs and /redoc.") + "\n"]
    for n, (cat, groups) in enumerate(cats.items(), 1):
        out.append(f"### 5.{n} {cat}\n")
        for g, rs in groups.items():
            out.append(f"#### {g}\n")
            out.append(table((t("Méthode", "Method"), t("Chemin", "Path"), t("Résumé", "Summary"), t("Accès", "Access")),
                             [(r["method"], code(r["path"]), r["summary"], access(r)) for r in rs]))
            if ctx.get("api_detail"):
                for r in rs:
                    out.append(f"**{r['method']} `{r['path']}`** — {r['summary']}\n")
                    if r.get("description"):
                        out.append(" ".join(r["description"].split()) + "\n")
                    params = [(code(p["name"]), p["in"], p["type"], p["required"], p["description"]) for p in r.get("parameters") or []]
                    fields = [(code(f["name"]), t("corps", "body"), f["type"], f["required"], f["description"]) for f in (r.get("request") or {}).get("fields") or []]
                    if params or fields:
                        out.append(table((t("Paramètre", "Parameter"), t("Dans", "In"), t("Type", "Type"), t("Requis", "Required"), t("Description", "Description")), params + fields))
    return "\n".join(out)


_CLI = (("liberty-next", "liberty.main", None), ("liberty-connectors", "liberty.cli", "build_parser"),
        ("liberty-admin", "liberty.admin_cli", "build_parser"), ("liberty-migrate", "liberty.migrate_cli", "build_parser"),
        ("liberty-crypto", "liberty.crypto_cli", None), ("liberty-license", "liberty.license_cli", None),
        ("liberty-ops", "liberty.ops_cli", None))


def _subcommands(module: Any, builder: str | None) -> list[tuple[str, str]]:
    import argparse
    parser = getattr(module, builder)() if builder and hasattr(module, builder) else None
    if parser is None:
        return []
    out: list[tuple[str, str]] = []
    for action in parser._actions:  # noqa: SLF001 — argparse keeps its sub-commands there
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            helps = {a.dest: a.help or "" for a in action._choices_actions}  # noqa: SLF001
            out += [(name, helps.get(name, "")) for name in action.choices]
    return out


def cli(t: Lang, ctx: dict[str, Any]) -> str:
    out = [f"## 6. {t('Ligne de commande', 'Command line')}\n",
           t("Les commandes installées avec le paquet ; chacune affiche son aide avec --help. Sous Docker, la commande unique ./liberty "
             "(release/liberty) enveloppe l'installation, la mise à jour, les sauvegardes et la base de données.",
             "The commands installed with the package; each prints its help with --help. Under Docker, the single ./liberty command "
             "(release/liberty) wraps install, upgrade, backups and the database.") + "\n"]
    for n, (name, mod_name, builder) in enumerate(_CLI, 1):
        try:
            module = importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001 — an optional extra missing must not break the report
            continue
        out.append(f"### 6.{n} {name}\n\n" + (first_sentence(module.__doc__, 400) or "") + "\n")
        subs = _subcommands(module, builder)
        if subs:
            out.append(table((t("Commande", "Command"), t("Rôle", "Purpose")), [(code(s), h) for s, h in subs]))
    return "\n".join(out)


def build(t: Lang, ctx: dict[str, Any]) -> str:
    out = [f"## 7. {t('Compilation et paquetage', 'Build and packaging')}\n",
           f"### 7.1 {t('Étapes', 'Steps')}\n",
           "\n".join(f"- {s}" for s in (
               t("**Interface** : npm ci, eslint, vitest, tsc puis vite build ; le résultat (frontend/dist) est servi par le serveur.",
                 "**Interface**: npm ci, eslint, vitest, tsc then vite build; the output (frontend/dist) is served by the server."),
               t("**Paquet** : une roue Python (liberty-next) publiée sur PyPI, les dépendances figées par requirements.txt (pip-compile de [all]).",
                 "**Package**: a Python wheel (liberty-next) published on PyPI, the dependencies locked by requirements.txt (pip-compile of [all])."),
               t("**Image** : ghcr.io/fblettner/liberty-next, multi-architecture, utilisateur 1000 ; les applications s'y ajoutent par leur propre roue (liberty-apps).",
                 "**Image**: ghcr.io/fblettner/liberty-next, multi-arch, user 1000; the applications are layered in from their own wheel (liberty-apps)."),
               t("**Publication** : un push sur main lance les contrôles, exige la note de version, construit la roue et l'image, pose le tag et publie.",
                 "**Release**: a push to main runs the checks, requires the release note, builds the wheel and the image, tags and publishes."))) + "\n"]
    try:
        meta = md.metadata("liberty-next")
        reqs = md.requires("liberty-next") or []
    except md.PackageNotFoundError:
        meta, reqs = None, []
    if meta is not None:
        base = [r for r in reqs if "extra ==" not in r]
        extras = Counter(r.split("extra ==")[1].strip().strip("'\"") for r in reqs if "extra ==" in r)
        out.append(f"### 7.2 {t('Paquet', 'Package')}\n\n" + table((t("Élément", "Item"), t("Valeur", "Value")), [
            (t("Version", "Version"), meta.get("Version")), ("Python", meta.get("Requires-Python")),
            (t("Extras", "Extras"), ", ".join(f"{e} ({c})" for e, c in sorted(extras.items())))]))
        out.append(f"### 7.3 {t('Dépendances', 'Dependencies')}\n\n" + table((t("Dépendance", "Dependency"),), [(code(r.split(";")[0]),) for r in sorted(base)]))
    rows = []
    for pkg in sorted(p for p in _ROOT.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        files = [f for f in pkg.rglob("*.py") if "__pycache__" not in f.parts]
        if files:
            rows.append((f"liberty/{pkg.name}/", len(files), sum(f.read_text(encoding="utf-8", errors="ignore").count("\n") for f in files)))
    top = [f for f in _ROOT.glob("*.py")]
    rows.insert(0, ("liberty/", len(top), sum(f.read_text(encoding="utf-8", errors="ignore").count("\n") for f in top)))
    out.append(f"### 7.4 {t('Contenu du paquet', 'Package content')}\n\n" + table((t("Dossier", "Folder"), t("Fichiers", "Files"), t("Lignes", "Lines")), rows, align_right=(1, 2)))
    dist = ctx.get("frontend_dir")
    if dist and Path(dist).is_dir():
        assets = [f for f in Path(dist).rglob("*") if f.is_file()]
        out.append(f"### 7.5 {t('Interface compilée', 'Built interface')}\n\n" + t(
            f"{len(assets)} fichiers, {sum(f.stat().st_size for f in assets) // 1024} Ko.", f"{len(assets)} files, {sum(f.stat().st_size for f in assets) // 1024} KB.") + "\n")
    return "\n".join(out)


def appendix(t: Lang, ctx: dict[str, Any]) -> str:
    keys = config_reference()
    out = [f"## 8. {t('Annexe : références dans le code', 'Appendix: code references')}\n",
           t("Pour chaque clé de configuration, les fichiers qui la lisent et le nombre de points d'appel.",
             "For each configuration key, the files that read it and the number of call sites.") + "\n",
           f"### 8.1 {t('Clés de configuration', 'Configuration keys')}\n"]
    rows = []
    for k in keys:
        if not k["readers"]:
            continue
        files = Counter(r["file"] for r in k["readers"])
        rows.append((code(f"{k['section']}.{k['key']}"), ", ".join(f"{f} ({n})" for f, n in files.items())))
    out.append(table((t("Clé", "Key"), t("Fichiers (points d'appel)", "Files (call sites)")), rows))
    if ctx.get("snippets"):
        out.append(f"### 8.2 {t('Extraits', 'Snippets')}\n")
        for k in keys:
            if k["readers"]:
                out.append(f"#### {k['section']}.{k['key']}\n\n" + "\n".join(f"- `{r['file']}:{r['line']}` — `{r['snippet'].replace('`', '')}`" for r in k["readers"]) + "\n")
    return "\n".join(out)


SECTIONS = {"scope": scope, "architecture": architecture, "data": data, "configuration": configuration,
            "api": api, "cli": cli, "build": build, "appendix": appendix}


def render(sections: list[str], lang: str, ctx: dict[str, Any]) -> str:
    t = Lang(lang)
    ctx.setdefault("date", date.today().isoformat())
    return "\n".join(SECTIONS[s](t, ctx) for s in SECTIONS if s in sections)
