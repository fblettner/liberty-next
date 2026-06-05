// Save one screen row, splitting its columns across the main table and any related 1:1
// write-back groups (Screen.column_groups). Used by BOTH the dialog Save and the grid inline/bulk
// Save so the multi-table split lives in exactly one place.
//
// Read model: the screen's read query JOINs each related table, so every column comes back in one
// result and each carries a ``group`` (the related table it belongs to, or none for the main
// table). Write model: on Save we partition the collected values by ``column.group`` and write
// each table separately —
//   · main table  → update_query / insert_query (captures the server-assigned PK on add)
//   · each group  → its update_query / insert_query, linked by param_binds and keyed off whether
//                   the group's key_columns came back non-null from the JOIN (a LEFT-JOIN miss ⇒ no
//                   related row yet ⇒ insert, else update).
//
// NOTE: the writes are sequential /api/sql POSTs, NOT one DB transaction (same trade-off as the
// embedded-form save) — the main write lands first so a later group failure leaves the main row
// written; the caller surfaces the error and the operator retries.
import { api } from '../../api/client'
import type { Column } from '../../types/connectors'
import type { ColumnGroup } from '../../types/screens'
import { originalKeys, resolveBindList, valueFor, withUpper, type Row } from './dialogHelpers'

export interface SaveScreenRowArgs {
  /** The screen's effective connector (the main write queries live here). */
  connector: string
  mode: 'add' | 'edit'
  /** The values to write, keyed by column/field name (already filtered for visibility + passwords
   *  by the caller). Split across the main table + each group by each column's ``group``. */
  sent: Row
  /** The original loaded row (full), for the migrated _put's ``:_ORIGINAL`` WHERE binds + deciding
   *  whether each group's related row already exists. */
  savedRow: Row
  /** The result columns — each carries its ``group`` (or undefined for the main table). */
  columns: Column[]
  /** The screen's related-table write-back groups. */
  columnGroups: ColumnGroup[]
  mainUpdateQuery?: string | null
  mainInsertQuery?: string | null
  /** Optional: true when a column name holds a password (its original is never rebound). */
  isPassword?: (name: string) => boolean
}

const enc = encodeURIComponent

/** Write the main row + each related group. Returns the main write's rowcount + the server-assigned
 *  binds (the sequence PK on add) so the caller can thread them into on_save / nested savers. */
export async function saveScreenRow(a: SaveScreenRowArgs): Promise<{ rowcount: number; resolvedBinds: Row }> {
  const groupByCol = new Map<string, string | null>()
  for (const c of a.columns) groupByCol.set(c.name.toLowerCase(), c.group ?? null)
  const groupOf = (name: string) => groupByCol.get(name.toLowerCase()) ?? null
  const isPw = (name: string) => a.isPassword?.(name) ?? false

  // Partition the values by their column's group.
  const mainSent: Row = {}
  const byGroup: Record<string, Row> = {}
  for (const [k, v] of Object.entries(a.sent)) {
    const g = groupOf(k)
    if (g) (byGroup[g] ??= {})[k] = v
    else mainSent[k] = v
  }

  const isEdit = a.mode === 'edit'

  // ── main table ──
  const mainQuery = isEdit ? a.mainUpdateQuery : a.mainInsertQuery
  if (!mainQuery) throw new Error(isEdit ? 'No update query on this screen.' : 'No insert query on this screen.')
  // Original values of the MAIN columns only (skip group columns + passwords) → SET untouched + the
  // :_ORIGINAL WHERE rebind.
  const mainSaved: Row = {}
  if (isEdit) {
    for (const [k, v] of Object.entries(a.savedRow)) {
      if (groupOf(k) || isPw(k)) continue
      mainSaved[k] = v
    }
  }
  const mainParams = isEdit ? { ...mainSaved, ...mainSent, ...originalKeys(mainSaved) } : mainSent
  const resp = await api.post<{ rowcount?: number; resolved_binds?: Row }>(
    `/api/sql/${enc(a.connector)}/${enc(mainQuery)}`, { params: withUpper(mainParams) },
  )
  const resolved = resp.resolved_binds ?? {}

  // ── related groups ──
  for (const grp of a.columnGroups) {
    const gvals = byGroup[grp.id]
    if (!gvals || Object.keys(gvals).length === 0) continue   // nothing changed for this group
    const gConn = grp.connector || a.connector
    // The related row exists iff its key columns came back non-null from the JOIN.
    const keyCols = grp.key_columns ?? []
    const exists = keyCols.length > 0 && keyCols.every((kc) => valueFor(kc, a.savedRow) != null)
    const gQuery = exists ? grp.update_query : grp.insert_query
    if (!gQuery) continue   // no insert query + no existing row → nothing to do
    // FK link: ``source`` reads the parent's value — prefer the freshly-assigned PK (add), else the
    // current/edited main value.
    const fk = resolveBindList(grp.param_binds, { ...a.savedRow, ...mainSent, ...resolved })
    let gParams: Row
    if (exists) {
      // Original of this group's columns + its key columns → SET untouched + WHERE :key_ORIGINAL.
      const gSaved: Row = {}
      for (const [k, v] of Object.entries(a.savedRow)) {
        if (groupOf(k) === grp.id && !isPw(k)) gSaved[k] = v
      }
      for (const kc of keyCols) { const v = valueFor(kc, a.savedRow); if (v != null) gSaved[kc] = v }
      gParams = { ...gSaved, ...gvals, ...fk, ...originalKeys(gSaved) }
    } else {
      gParams = { ...gvals, ...fk }
    }
    await api.post(`/api/sql/${enc(gConn)}/${enc(gQuery)}`, { params: withUpper(gParams) })
  }

  return { rowcount: resp.rowcount ?? 0, resolvedBinds: resolved }
}
