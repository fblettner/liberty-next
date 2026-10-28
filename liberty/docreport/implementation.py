"""The implementation report (C12): technical and functional documentation of this install, read from its
configuration and running state — context, enabled features, applications and menus, screens, connectors
and pools, dashboards / charts / Explorer models, jobs and notification rules, roles and users, reference
data, and the settings and API as appendices. Secrets are masked."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

from liberty.docreport.md import Lang, aside, cell, code, first_sentence, table

MASK = "••••••"
_SECRET = re.compile(r"(password|secret|api_key|master_key|token|key)$", re.I)
_URL_PASSWORD = re.compile(r"(://[^:/@\s]+:)[^@/\s]+@")


def _mask(name: str, value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("ENC:") or (value and _SECRET.search(name) and not value.startswith("${")):
            return MASK
        return _URL_PASSWORD.sub(rf"\1{MASK}@", value)
    return value


def _raw(state: Any, filename: str) -> dict[str, Any]:
    from liberty.web.dependencies import raw_config
    return raw_config(state, filename)


def _version(dist: str) -> str:
    import importlib.metadata as md
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return ""


def context(t: Lang, ctx: dict[str, Any]) -> str:
    s, state = ctx["settings"], ctx["state"]
    lic = getattr(state, "license", None)
    pools = (_raw(state, "connectors.toml").get("pools") or {})
    apps = list((getattr(getattr(state, "menus", None), "menus", None) or {}).keys())
    rows = [
        (t("Application", "Application"), s.app.name),
        (t("Version Liberty Next", "Liberty Next version"), _version("liberty-next") or "—"),
        (t("Applications (liberty-apps)", "Applications (liberty-apps)"), _version("liberty-apps") or t("non installées", "not installed")),
        (t("Applications du menu", "Menu applications"), apps),
        (t("Chemin d'accès", "Base path"), s.app.base_path or "/"),
        (t("Langue par défaut", "Default language"), s.app.default_language),
        (t("Pools de base de données", "Database pools"), list(pools)),
        (t("Licence", "Licence"), "—" if lic is None else f"{getattr(lic, 'mode', '')} · {getattr(lic, 'customer', '') or ''} · {', '.join(getattr(lic, 'apps', None) or [])}".strip(" ·")),
        (t("Dossier de configuration", "Configuration folder"), str(ctx.get("config_dir") or "—")),
        (t("Généré le", "Generated on"), f"{ctx['date']} {t('par', 'by')} {ctx['author']}"),
    ]
    return f"## 1. {t('Contexte', 'Context')}\n\n" + table((t("Élément", "Item"), t("Valeur", "Value")), rows)


def architecture(t: Lang, ctx: dict[str, Any]) -> str:
    s, state = ctx["settings"], ctx["state"]
    raw = _raw(state, "connectors.toml")
    conns = raw.get("connectors") or {}
    kinds = Counter(str((c or {}).get("type", "sql")) for c in conns.values())
    jobs = [j for j in _raw(state, "jobs.toml").get("jobs") or [] if isinstance(j, dict)]
    on, off = t("activé", "on"), t("désactivé", "off")
    rows = [
        (t("Comptes", "Accounts"), f"{t('fichier', 'file')} auth.toml" if s.auth.backend == "toml" else f"{t('base', 'database')} · pool {s.auth.pool or 'default'}"),
        ("OIDC", on if s.oidc.enabled else off),
        (t("Assistant IA", "AI assistant"), f"{on} · {s.ai.model}" if s.ai.enabled else off),
        (t("Tâches nomaflow", "nomaflow jobs"), f"{on if s.jobs.enabled else off} · {len(jobs)} {t('tâche(s)', 'job(s)')}, {sum(1 for j in jobs if j.get('enabled', True) and j.get('schedule'))} {t('planifiée(s)', 'scheduled')}"),
        (t("Notifications", "Notifications"), f"{on if s.notify.enabled else off} · SMTP {s.notify.smtp_host or t('non configuré', 'not set')}"),
        (t("Paquets de changements", "Change packages"), on if s.changesets.enabled else off),
        (t("Rechargement à chaud", "Hot reload"), on if s.app.hot_reload else off),
        (t("Cibles de promotion", "Promotion targets"), len(s.promotion.targets or {})),
        (t("Connecteurs", "Connectors"), ", ".join(f"{n} {k.upper()}" for k, n in kinds.items()) or "—"),
        (t("Oracle (mode thick)", "Oracle (thick mode)"), on if s.app.oracle_thick else off),
    ]
    return (f"## 2. {t('Architecture', 'Architecture')}\n\n" + t("Les composants actifs sur cette installation, tels que la configuration les décrit.",
            "The components active on this install, as the configuration describes them.") + "\n\n"
            + table((t("Composant", "Component"), t("État", "State")), rows)
            + "\n" + aside(t("**Lecture :** un composant « désactivé » est configurable dans Paramètres › App ; le rapport technique décrit son fonctionnement.",
                             "**Reading:** an \"off\" component is set in Settings › App; the technical report describes how it works.")))


def applications(t: Lang, ctx: dict[str, Any]) -> str:
    menus = getattr(getattr(ctx["state"], "menus", None), "menus", None) or {}
    screens = getattr(getattr(ctx["state"], "screens", None), "screens", None) or {}
    out = [f"## 3. {t('Applications et menus', 'Applications and menus')}\n",
           t("Chaque application, ses entrées de menu dans l'ordre, ce qu'elles ouvrent et les rôles qui les voient.",
             "Each application, its menu entries in order, what they open and the roles that see them.") + "\n"]
    for n, (app, menu) in enumerate(menus.items(), 1):
        items = list(getattr(menu, "items", None) or [])
        out.append(f"### 3.{n} {getattr(menu, 'label', None) or app}\n\n" + t(
            f"{len(items)} entrée(s) de menu, {len(screens.get(app) or {})} écran(s).", f"{len(items)} menu entries, {len(screens.get(app) or {})} screen(s).") + "\n")
        out.append(table((t("Entrée", "Entry"), t("Parent", "Parent"), t("Type", "Type"), t("Ouvre", "Opens"), t("Rôles", "Roles")),
                         [(i.label, i.parent, i.type, (f"{i.connector}." if i.connector else "") + (i.target or ""), i.roles) for i in items]))
    return "\n".join(out)


def screens(t: Lang, ctx: dict[str, Any]) -> str:
    by_app = getattr(getattr(ctx["state"], "screens", None), "screens", None) or {}
    out = [f"## 4. {t('Écrans', 'Screens')}\n",
           t("Les écrans de chaque application : la requête lue, les écritures permises, les colonnes déclarées, la fenêtre de détail et les actions.",
             "Each application's screens: the read query, the writes allowed, the declared columns, the detail dialog and the actions.") + "\n"]
    for n, (app, items) in enumerate(by_app.items(), 1):
        rows = []
        for sid, sc in items.items():
            writes = "".join(x for x, q in (("U", sc.update_query), ("I", sc.insert_query), ("D", sc.delete_query)) if q)
            dialog = getattr(sc, "dialog", None)
            tabs = len(getattr(dialog, "tabs", None) or []) if dialog is not None else 0
            rows.append((code(sid), sc.label or first_sentence(sc.description, 50), f"{sc.connector or app}.{sc.read_query}", writes,
                         len(sc.columns or []), tabs, len(getattr(sc, "actions", None) or []), sc.audit_table))
        out.append(f"### 4.{n} {app}\n\n" + table((t("Écran", "Screen"), t("Libellé", "Label"), t("Requête", "Query"), t("Écritures", "Writes"),
                                                    t("Colonnes", "Columns"), t("Onglets", "Tabs"), t("Actions", "Actions"), t("Audit", "Audit")),
                                                   rows, align_right=(4, 5, 6)))
    out.append(aside(t("**Écritures** : U = modification, I = création, D = suppression. Une requête d'écriture exige aussi writable = true et la permission du rôle.",
                       "**Writes**: U = update, I = insert, D = delete. A write query also needs writable = true and the role's permission.")))
    return "\n".join(out)


def connectors(t: Lang, ctx: dict[str, Any]) -> str:
    state = ctx["state"]
    raw = _raw(state, "connectors.toml")
    reg = getattr(state, "connectors", None)
    loaded = set(reg.names()) if reg is not None else set()
    pools = raw.get("pools") or {}
    out = [f"## 5. {t('Connecteurs et pools', 'Connectors and pools')}\n", f"### 5.1 {t('Pools', 'Pools')}\n",
           table((t("Pool", "Pool"), t("Base", "Database"), t("URL", "URL"), t("Lignes max.", "Max rows"), t("SQL journalisé", "SQL logged")),
                 [(p, str((c or {}).get("url", "")).split(":", 1)[0].split("+")[0], _mask("url", (c or {}).get("url")), (c or {}).get("max_rows"),
                   bool((c or {}).get("debug_sql"))) for p, c in pools.items()]),
           f"### 5.2 {t('Connecteurs', 'Connectors')}\n"]
    rows = []
    for name, c in (raw.get("connectors") or {}).items():
        c = c or {}
        kind = str(c.get("type", "sql"))
        items = len(c.get("tables") or []) if kind == "sql" else len(c.get("endpoints") or {})
        state_txt = t("chargé", "loaded") if name in loaded else (t("non chargé (licence)", "not loaded (licence)") if c.get("licensed") else t("non chargé", "not loaded"))
        rows.append((code(name), kind.upper(), c.get("pool") or _mask("base_url", c.get("base_url")), items, c.get("dd_prefix"), bool(c.get("licensed")), state_txt))
    out.append(table((t("Connecteur", "Connector"), t("Type", "Type"), t("Pool / URL", "Pool / URL"), t("Tables / points", "Tables / endpoints"),
                      t("Préfixe dictionnaire", "Dictionary prefix"), t("Sous licence", "Licensed"), t("État", "State")), rows, align_right=(3,)))
    return "\n".join(out)


def analytics(t: Lang, ctx: dict[str, Any]) -> str:
    state = ctx["state"]
    boards = _raw(state, "dashboards.toml").get("dashboards") or {}
    charts = _raw(state, "charts.toml").get("charts") or {}
    models = _raw(state, "explorer.toml").get("explorer") or {}
    out = [f"## 6. {t('Tableaux de bord, graphiques et Explorateur', 'Dashboards, charts and Explorer')}\n", f"### 6.1 {t('Tableaux de bord', 'Dashboards')}\n"]
    rows = []
    for app, items in boards.items():
        for bid, b in (items or {}).items():
            widgets = [w for w in (b or {}).get("widgets") or [] if isinstance(w, dict)]
            kinds = Counter(str(w.get("type", "chart")) for w in widgets)
            rows.append((f"{app}.{bid}", (b or {}).get("label"), len(widgets), ", ".join(f"{k} {n}" for k, n in kinds.items())))
    out.append(table((t("Tableau de bord", "Dashboard"), t("Libellé", "Label"), t("Blocs", "Widgets"), t("Types", "Types")), rows, align_right=(2,)) or t("_Aucun._", "_None._") + "\n")
    out.append(f"### 6.2 {t('Graphiques', 'Charts')}\n")
    out.append(table((t("Graphique", "Chart"), t("Libellé", "Label"), t("Requête", "Query"), t("Type", "Type")),
                     [(f"{sc}.{cid}", (c or {}).get("label"), f"{(c or {}).get('connector', sc)}.{(c or {}).get('query')}", ((c or {}).get("spec") or {}).get("type", "bar"))
                      for sc, items in charts.items() for cid, c in (items or {}).items()]) or t("_Aucun._", "_None._") + "\n")
    out.append(f"### 6.3 {t('Modèles de l’Explorateur', 'Explorer models')}\n")
    out.append(table((t("Modèle", "Model"), t("Libellé", "Label"), t("Entité pivot", "Hub"), t("Entités", "Entities"), t("Relations", "Relations"),
                      t("Analyses livrées", "Bundled analyses"), t("Tableaux livrés", "Bundled dashboards")),
                     [(f"{app}.{mid}", (m or {}).get("label"), (m or {}).get("hub") or (m or {}).get("preset"), len((m or {}).get("entities") or {}),
                       len((m or {}).get("relations") or []), len((m or {}).get("analyses") or {}), len((m or {}).get("dashboards") or {}))
                      for app, items in models.items() for mid, m in (items or {}).items() if isinstance(m, dict)],
                     align_right=(3, 4, 5, 6)) or t("_Aucun._", "_None._") + "\n")
    return "\n".join(out)


def operations(t: Lang, ctx: dict[str, Any]) -> str:
    state = ctx["state"]
    raw_jobs = _raw(state, "jobs.toml")
    jobs = [j for j in raw_jobs.get("jobs") or [] if isinstance(j, dict)]
    rules = getattr(getattr(state, "notification_rules", None), "notifications", None) or {}
    actions = _raw(state, "actions.toml").get("actions") or {}
    out = [f"## 7. {t('Exploitation', 'Operations')}\n", f"### 7.1 {t('Tâches planifiées', 'Scheduled jobs')}\n"]
    out.append(table((t("Tâche", "Job"), t("Description", "Description"), t("Planification", "Schedule"), t("Active", "Enabled"), t("Étapes", "Steps"), t("Types d'étapes", "Step types")),
                     [(code(j.get("id")), first_sentence(j.get("description"), 80), j.get("schedule") or t("manuelle", "manual"), bool(j.get("enabled", True)),
                       len(j.get("steps") or []), sorted({str(s.get("type")) for s in j.get("steps") or [] if isinstance(s, dict)})) for j in jobs],
                     align_right=(4,)) or t("_Aucune._", "_None._") + "\n")
    retention = ((raw_jobs.get("meta") or {}).get("retention") or {})
    if retention:
        out.append(t(f"Rétention des exécutions : {retention.get('days', '—')} jour(s), {retention.get('keep_last_per_job', '—')} dernières par tâche.",
                     f"Run retention: {retention.get('days', '—')} day(s), last {retention.get('keep_last_per_job', '—')} per job.") + "\n")
    out.append(f"### 7.2 {t('Règles de notification', 'Notification rules')}\n")
    rows = []
    for rid, r in rules.items():
        trig = r.trigger
        what = {"screen": lambda x: f"{x.app}.{x.screen} · {', '.join(x.events)}", "job": lambda x: f"{t('tâches', 'jobs')} {', '.join(x.jobs) or '*'} · {', '.join(x.events)}"}.get(
            getattr(trig, "kind", ""), lambda x: getattr(x, "kind", ""))(trig)
        rows.append((code(rid), r.label, bool(r.enabled), what, r.channels, r.recipients))
    out.append(table((t("Règle", "Rule"), t("Libellé", "Label"), t("Active", "Enabled"), t("Déclencheur", "Trigger"), t("Canaux", "Channels"), t("Destinataires", "Recipients")), rows)
               or t("_Aucune._", "_None._") + "\n")
    out.append(f"### 7.3 {t('Actions partagées', 'Shared actions')}\n")
    out.append(table((t("Action", "Action"), t("Libellé", "Label")), [(code(a), (v or {}).get("label") if isinstance(v, dict) else "") for a, v in actions.items()])
               or t("_Aucune._", "_None._") + "\n")
    return "\n".join(out)


async def security(t: Lang, ctx: dict[str, Any]) -> str:
    s, backend = ctx["settings"], getattr(ctx["state"], "auth_backend", None)
    users = await backend.list_users() if backend is not None else []
    roles = await backend.list_roles() if backend is not None else []
    per_role = Counter(r for u in users for r in u.roles)
    out = [f"## 8. {t('Sécurité', 'Security')}\n",
           table((t("Élément", "Item"), t("Valeur", "Value")), [
               (t("Comptes", "Accounts"), s.auth.backend), ("OIDC", s.oidc.enabled),
               (t("Jeton d'accès (s)", "Access token (s)"), s.auth.access_token_ttl), (t("Jeton de rafraîchissement (s)", "Refresh token (s)"), s.auth.refresh_token_ttl),
               (t("Échecs de connexion tolérés", "Sign-in failures allowed"), f"{s.auth.login_max_failures} / {s.auth.login_window_seconds} s")]),
           f"### 8.1 {t('Rôles', 'Roles')}\n",
           table((t("Rôle", "Role"), t("Description", "Description"), t("Permissions", "Permissions"), t("Règles de périmètre", "Scope rules"), t("Utilisateurs", "Users")),
                 [(code(name), desc, len(perms), len(scope or []), per_role.get(name, 0)) for name, perms, desc, scope in roles], align_right=(2, 3, 4)) or t("_Aucun._", "_None._") + "\n",
           f"### 8.2 {t('Utilisateurs', 'Users')}\n\n" + t(f"{len(users)} utilisateur(s), dont {sum(1 for u in users if u.is_active)} actif(s).",
                                                            f"{len(users)} user(s), {sum(1 for u in users if u.is_active)} active.") + "\n",
           table((t("Identifiant", "Username"), t("Nom", "Name"), t("Rôles", "Roles"), t("Actif", "Active"), t("Superutilisateur", "Superuser"), t("Origine", "Provider")),
                 [(u.username, u.full_name, u.roles, u.is_active, u.is_superuser, u.provider) for u in users])]
    return "\n".join(out)


def reference(t: Lang, ctx: dict[str, Any]) -> str:
    reg = getattr(ctx["state"], "connectors", None)
    d = getattr(reg, "dictionary", None)
    out = [f"## 9. {t('Référentiels', 'Reference data')}\n",
           t("Le dictionnaire partagé puis celui de chaque connecteur : libellés, listes de valeurs, recherches et séquences.",
             "The shared dictionary, then each connector's: labels, enums, lookups and sequences.") + "\n"]
    if d is None:
        return "\n".join(out) + t("_Dictionnaire absent._", "_No dictionary._") + "\n"
    scopes = [(t("partagé", "shared"), d)] + list((getattr(d, "connectors", None) or {}).items())
    out.append(table((t("Portée", "Scope"), t("Entrées", "Entries"), t("Listes de valeurs", "Enums"), t("Recherches", "Lookups"), t("Séquences", "Sequences")),
                     [(name, len(x.entries), len(x.enums), len(x.lookups), len(x.sequences)) for name, x in scopes], align_right=(1, 2, 3, 4)))
    enums = [(name, eid, e) for name, x in scopes for eid, e in x.enums.items()]
    out.append(f"### 9.1 {t('Listes de valeurs', 'Enums')}\n")
    out.append(table((t("Portée", "Scope"), t("Liste", "Enum"), t("Libellé", "Label"), t("Valeurs", "Values")),
                     [(name, code(eid), e.label, ", ".join(f"{v.value}={v.label or v.value}" for v in e.values[:12]) + (" …" if len(e.values) > 12 else ""))
                      for name, eid, e in enums]) or t("_Aucune._", "_None._") + "\n")
    lookups = [(name, lid, lk) for name, x in scopes for lid, lk in x.lookups.items()]
    out.append(f"### 9.2 {t('Recherches', 'Lookups')}\n")
    out.append(table((t("Portée", "Scope"), t("Recherche", "Lookup"), t("Source", "Source")),
                     [(name, code(lid), ".".join(str(v) for v in (getattr(lk, "connector", None), getattr(lk, "query", None)) if v)) for name, lid, lk in lookups])
               or t("_Aucune._", "_None._") + "\n")
    seqs = [(name, sid, sq) for name, x in scopes for sid, sq in x.sequences.items()]
    out.append(f"### 9.3 {t('Séquences', 'Sequences')}\n")
    out.append(table((t("Portée", "Scope"), t("Séquence", "Sequence"), t("Description", "Description")),
                     [(name, code(sid), getattr(sq, "description", None)) for name, sid, sq in seqs]) or t("_Aucune._", "_None._") + "\n")
    return "\n".join(out)


def appendix_config(t: Lang, ctx: dict[str, Any]) -> str:
    out = [f"## 10. {t('Annexe A. Réglages', 'Appendix A. Settings')}\n",
           t("Toutes les valeurs de app.toml telles que l'instance les lit, secrets masqués.", "Every app.toml value as the instance reads it, secrets masked.") + "\n"]
    dumped = ctx["settings"].model_dump(mode="json")
    for section, values in dumped.items():
        if not isinstance(values, dict):
            continue
        out.append(f"### [{section}]\n")
        out.append(table((t("Clé", "Key"), t("Valeur", "Value")),
                         [(code(k), cell(_mask(k, v)) if not isinstance(v, (dict, list)) or not v else
                           (MASK if _SECRET.search(k) else str(v)[:160])) for k, v in values.items()]))
    return "\n".join(out)


def appendix_api(t: Lang, ctx: dict[str, Any]) -> str:
    from liberty.docreport.technical import api
    body = api(t, {**ctx, "api_detail": True})
    return body.replace("## 5. ", f"## 11. {t('Annexe B. ', 'Appendix B. ')}", 1).replace("### 5.", "### 11.")


SECTIONS: dict[str, Any] = {
    "context": context, "architecture": architecture, "applications": applications, "screens": screens, "connectors": connectors,
    "analytics": analytics, "operations": operations, "security": security, "reference": reference,
    "appendixConfig": appendix_config, "appendixApi": appendix_api,
}


async def render(sections: list[str], lang: str, ctx: dict[str, Any]) -> str:
    import inspect
    t = Lang(lang)
    ctx.setdefault("date", date.today().isoformat())
    parts = []
    for sid, fn in SECTIONS.items():
        if sid in sections:
            part = fn(t, ctx)
            parts.append(await part if inspect.isawaitable(part) else part)
    return "\n".join(parts)
