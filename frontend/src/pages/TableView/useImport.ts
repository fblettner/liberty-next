// The grid's import — rows brought in from an Excel / CSV file (the in-grid quick import and the
// validated Import dialog) or pasted from Excel: header-text mapping, screen-rule validation, the
// lookup return-fill. Owned by ResultTable; the rows land in the batch edit (``useBatchEdit``).
import { useCallback, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import * as XLSX from 'xlsx'
import type { Column, QueryResult } from '../../types/connectors'
import { loadLookup, lookupKey, type LookupData, type LookupSpec } from '../../services/lookups'
import { activeRulesWhen } from './dialogHelpers'
import type { DataRow } from './resultColumns'
import type { useBatchEdit } from './useBatchEdit'

type BatchEdit = ReturnType<typeof useBatchEdit>

export function useImport({
  result, keyColumns, specForRule, lookupMaps,
  editMode, setEditMode, editsRef, setDirtyRows, setSaveErrors, prependNewRows,
}: {
  result: QueryResult
  /** Result columns that identify a row — the import matches existing rows on them. */
  keyColumns?: string[]
  /** The grid's (rule, binds, row) → the lookup to fetch — the validation resolves each row's list with it. */
  specForRule: (r: Column['rule'] | null | undefined, binds: { param: string; value?: string | null; source?: string | null }[] | undefined,
    rowValues: Record<string, unknown>) => LookupSpec | null
  lookupMaps: Map<string, LookupData>
} & Pick<BatchEdit, 'editMode' | 'setEditMode' | 'editsRef' | 'setDirtyRows' | 'setSaveErrors' | 'prependNewRows'>) {
  const { t } = useTranslation()
  const fileRef = useRef<HTMLInputElement>(null)
  // The validated Import dialog (modal-first: it handles upload + mode + check + save internally).
  const [importOpen, setImportOpen] = useState(false)

  // ── Import: SCREEN-RULE validation, reusing the grid's OWN resolution ──
  // The DB can't tell a bad lookup/enum value from a good one (a JDE CHAR column accepts any junk),
  // so we check each cell against its column's rule — the per-row ACTIVE rule (rules_when: FSDTAI is a
  // data item for some sec_types, a form name for others; FSDLT's Y/N flips), matched trim-tolerantly.
  // A LOOKUP is checked against THIS row's list — ``specForRule`` with the row's binds, the grid's own
  // resolution — loaded on demand (``loadLookup``, the session cache): a list the grid has not loaded
  // yet (the screen just opened, or no grid row uses this narrowing) is fetched, never skipped; one
  // that cannot be loaded makes the row an error rather than a silent pass.
  const validateImportRow = useCallback(async (row: Record<string, unknown>): Promise<string[]> => {
    const errs: string[] = []
    for (const c of result.columns) {
      const raw = row[c.name]
      if (raw == null || String(raw).trim() === '') continue   // empty → DB defaults / nullability decide
      const val = String(raw).trim()
      const name = c.label || c.name
      // The rule that ACTUALLY applies to this row (rules_when), not the base rule.
      const aw = c.rules_when?.length ? activeRulesWhen(c.rules_when, row) : null
      const rule = aw ? (aw.rule ?? null) : (c.rule ?? null)
      if (rule?.kind === 'lookup') {
        const spec = specForRule(rule, aw ? aw.lookup_param_binds : undefined, row)
        if (!spec) continue
        let codes: Set<string>
        try {
          codes = new Set([...(await loadLookup(spec)).map.keys()].map((k) => k.trim()))
        } catch {
          errs.push(`${name}: ${t('import.listUnavailable')}`)
          continue
        }
        if (codes.size > 0 && !codes.has(val)) errs.push(`${name}: “${val}” is not a valid value`)
      } else if (rule?.kind === 'enum') {
        if (!rule.values.some((v) => v.value === val || v.value.trim() === val)) errs.push(`${name}: “${val}” is not an allowed value`)
      } else if (rule?.kind === 'boolean') {
        // Only a COMPLETE boolean (both tokens) is checkable — a one-sided flag can't be validated
        // without false positives across JDE's mixed Y/N · 0/1 · blank conventions.
        const tv = rule.true_value
        const fv = rule.false_value
        if (tv != null && fv != null && val !== String(tv).trim() && val !== String(fv).trim()) {
          errs.push(`${name}: “${val}” must be ${tv} or ${fv}`)
        }
      }
    }
    return errs
  }, [result.columns, specForRule, t])

  // Import rows from an Excel/CSV file. Matching is by *header text*, not column position, so the
  // sheet's columns can be in any order and extra columns are ignored: we build `byHeader` =
  // {normalized header text → result column name}, registering for each result column its `name`,
  // its display `label`, and the "(ID)" suffixed forms (a lookup column shows as "<label> (ID)" in
  // the grid, so that's a natural header to round-trip). `sheet_to_json` already gives each row as
  // {headerText → value} (the first sheet row is the header), so we just look each header up.
  //
  // Then, if the query has `keyColumns`, each imported row is matched against the *loaded* rows on
  // those columns: a match becomes an **edit** of that row (→ `update_query` on Save), the rest are
  // **new** rows (→ `insert_query`). That's the v2 replacement for v1's MERGE/UPSERT `_post` queries:
  // update-or-insert is decided here, in the batch-edit model, instead of in one SQL statement.
  // header-text → result column name, for round-tripping an export's headers (the column name, its
  // label, and the "(ID)"-suffixed forms a lookup/enum export emits). Shared by file import AND the
  // clipboard paste's header detection.
  const importByHeader = useMemo(() => {
    const idTag = t('table.idColumnSuffix')
    const m = new Map<string, string>()
    for (const c of result.columns) {
      const add = (s: string | null | undefined) => { if (s) m.set(s.trim().toLowerCase(), c.name) }
      add(c.name)
      // A coded column (LOOKUP/ENUM) resolves by its CODE only — its description isn't unique and the
      // code lives in the "(ID)" column the export emits. So map the code-bearing headers ("(ID)" +
      // raw name), not the bare label (which holds the non-unique description).
      if (c.rule?.kind !== 'lookup' && c.rule?.kind !== 'enum') add(c.label)
      add(`${c.label ?? c.name} ${idTag}`); add(`${c.name} ${idTag}`)
    }
    return m
  }, [result.columns, t])

  // Resolve lookup ``return_binds`` on a set of mapped rows: a coded column (e.g. OBNM) with
  // return_binds fills its mapped target columns (e.g. SY) from the matching lookup row — same effect
  // as a dialog/grid pick, driven by the imported/pasted code. Fill-if-empty (an explicit value
  // wins); skips silently when the lookup data isn't loaded. Mutates the rows in place.
  const applyReturnFill = useCallback((seeds: Record<string, unknown>[]) => {
    const returnFillCols = result.columns.filter((c) => c.rule?.kind === 'lookup' && (c.return_binds?.length ?? 0) > 0)
    if (returnFillCols.length === 0) return
    for (const seed of seeds) {
      for (const c of returnFillCols) {
        const rule = c.rule
        if (rule?.kind !== 'lookup') continue
        const code = seed[c.name]
        if (code == null || String(code).trim() === '') continue
        const data = lookupMaps.get(lookupKey({ connector: rule.connector, query: rule.query, value: rule.value, label: rule.label, params: rule.params, sources: rule.sources }))
        if (!data?.rows) continue
        const valKey = rule.value
        const want = String(code).trim()
        const row = data.rows.find((r) => {
          const rv = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
          return rv != null && String(rv).trim() === want
        })
        if (!row) continue
        const lcRow = new Map(Object.entries(row).map(([k, v]) => [k.toLowerCase(), v]))
        for (const b of c.return_binds ?? []) {
          const target = result.columns.find((rc) => rc.name.toLowerCase() === b.column.toLowerCase())?.name ?? b.column
          const cur = seed[target]
          if (cur != null && String(cur).trim() !== '') continue  // explicit value wins
          const v = lcRow.get(b.param.toLowerCase())
          if (v !== undefined) seed[target] = v
        }
      }
    }
  }, [result.columns, lookupMaps])

  // Parse + header-map a sheet into rows keyed by column name (with return-fill) — shared by the
  // in-grid import and the validated Import dialog. Null when empty / no header matched (sets banner).
  const parseImportFile = useCallback(async (file: File): Promise<Record<string, unknown>[] | null> => {
    const wb = XLSX.read(await file.arrayBuffer(), { type: 'array' })
    const ws = wb.Sheets[wb.SheetNames[0]]
    if (!ws) return null
    const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(ws, { defval: null })
    const seeds = rows.map((r) => {
      const out: Record<string, unknown> = {}
      for (const [header, v] of Object.entries(r)) {
        const col = importByHeader.get(header.trim().toLowerCase())
        if (col) out[col] = v
      }
      return out
    }).filter((s) => Object.keys(s).length > 0)  // skip rows whose headers matched nothing
    if (seeds.length === 0) { setSaveErrors([t('table.importNoMatch', { file: file.name })]); return null }
    applyReturnFill(seeds)
    return seeds
  }, [importByHeader, applyReturnFill, t, setSaveErrors])

  // Paste from Excel → new rows. The DataTable does the column mapping (it owns the exact visible-
  // column set/order the export uses, so it can't drift); here we just apply the lookup return-fill
  // and prepend the rows. Rows arrive keyed by column id, which IS the result column name.
  const onPasteRows = useCallback((rows: Record<string, unknown>[]) => {
    applyReturnFill(rows)
    prependNewRows(rows)
  }, [applyReturnFill, prependNewRows])

  // In-grid import (edit-mode quick add): parse + map, then merge into the bulk editor — existing
  // rows (matched on key) become pending edits, the rest new rows. The standalone Import button opens
  // the validated ImportDialog instead (which parses via ``parseImportFile`` itself).
  const importFile = useCallback(async (file: File) => {
    setSaveErrors([])
    try {
      const seeds = await parseImportFile(file)
      if (!seeds) return
      if (!editMode) setEditMode(true)

      // Effective key columns come from the per-column ``key`` flags resolved onto the RESULT
      // columns (the Columns-tab "key"), not ``screen.key_columns`` — that's the legacy explicit
      // override and is usually EMPTY even when the row has a key (F00950 keys via per-column flags).
      // An empty list meant every imported row looked new → re-import failed on duplicate-PK insert.
      // Using the result columns also guarantees the names match the seed keys' case.
      const keyColsFromResult = result.columns.filter((c) => c.key).map((c) => c.name)
      const keys = (keyColsFromResult.length ? keyColsFromResult : (keyColumns ?? [])).filter(Boolean)
      const newSeeds: Record<string, unknown>[] = []
      const matched: DataRow[] = []
      // Match on the key columns ACTUALLY PRESENT in the sheet. A hidden / derived key column (e.g.
      // JDE FSSY, auto-populated from the object) isn't exported, so requiring ALL keys would make
      // every imported row look new — and re-importing an existing row would then try to INSERT it
      // and fail on a duplicate PK. Compare TRIMMED (JDE pads CHAR columns; the read trims them).
      const norm = (v: unknown) => String(v ?? '').trim()
      const matchKeys = keys.filter((k) => seeds.some((s) => k in s))
      if (matchKeys.length > 0) {
        const keyOf = (o: Record<string, unknown>) => matchKeys.map((k) => norm(o[k])).join('\u0000')
        const byKey = new Map<string, DataRow>()
        for (const row of result.rows) byKey.set(keyOf(row), row)
        for (const seed of seeds) {
          const match = byKey.get(keyOf(seed))  // every matchKey is present in the seed by construction
          if (match) {  // overlay the imported fields onto the existing row as pending edits
            editsRef.current.set(match, { ...editsRef.current.get(match), ...seed })
            matched.push(match)
          } else {
            newSeeds.push(seed)
          }
        }
      } else {
        newSeeds.push(...seeds)
      }
      if (matched.length) setDirtyRows((s) => { const n = new Set(s); for (const r of matched) n.add(r); return n })
      if (newSeeds.length) prependNewRows(newSeeds)
    } catch (e) {
      setSaveErrors([e instanceof Error ? e.message : String(e)])
    }
  }, [parseImportFile, prependNewRows, editMode, result.columns, result.rows, keyColumns, editsRef, setDirtyRows, setEditMode, setSaveErrors])

  return { fileRef, importOpen, setImportOpen, validateImportRow, parseImportFile, onPasteRows, importFile }
}
