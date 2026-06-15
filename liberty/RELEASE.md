# Liberty Next — Release notes

## 7.0.46 — 2026-06-15

- _Release notes pending — edit liberty/RELEASE.md._

## 7.0.45 — 2026-06-14

- _Release notes pending — edit liberty/RELEASE.md._

## 7.0.44 — 2026-06-13

- _Release notes pending — edit liberty/RELEASE.md._

## 7.0.43 — 2026-06-13

- _Release notes pending — edit liberty/RELEASE.md._

## 7.0.42 — 2026-06-13

- _Release notes pending — edit liberty/RELEASE.md._

## 7.0.41 — 2026-06-13

**Reports**
- **Run-form dropdowns** — report parameters can declare `options`, and the run dialog renders a
  searchable select instead of a free input. Choices resolve server-side (a static list, the
  configured connectors, a connector's schemas, or a named connector query mapped to value/label),
  and cascade — e.g. an Application picker that lists apps by name on the chosen connector.

## 7.0.40 — 2026-06-12

**Docs**
- README: upgrade history & release notes, `release/upgrade.sh`, and a failed-publish recovery note
  (incl. the GitHub-App workflow-file tag-push limitation).

## 7.0.39 — 2026-06-12

**Config history**
- **Upgrade history** tracks software version changes on startup — framework **and** licensed apps,
  independently — surfaced under Settings → History → Upgrades with the release notes inline.
- **Release notes** shipped in the wheel (`RELEASE.md` / `.fr`), served per component.

## 7.0.27 — 2026-06-12

**Screen Creation Assistant**
- New superuser **Screen Creation Assistant** (sidebar, above Monitoring) — a guided wizard that turns
  picked tables into a live screen + dialog + menu in one pass: tables & joins, columns → tabs,
  dictionary review, and menu placement.
- **Catalog presets** — operator-managed table presets (`config/presets/`), browsable and searchable;
  selecting one pre-wires the joins. Ships a JD Edwards Address Book catalog.

**Dictionary**
- Shared scan table between the Dictionary editor and the assistant: existing entries are detected and
  only missing ones are proposed, with Format/Rule dropdowns, rule value, **UDC lookup params**, and
  default.

**Config history**
- **Upgrade history** — Settings → History → Upgrades records each application version change.
- **Release notes** — this view.

## 7.0.26 — 2026-06-01

- Screen + dependency **bundle versioning** (Phase 2): every screen save snapshots its dependency
  closure; restore reverts the screen *with* its queries/lookups/dictionary entries.
- **Retention purge** job for config-version snapshots; per-version and per-file delete.
- Read-only screen mode; move a field between dialog tabs from the inspector.
