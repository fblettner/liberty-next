// The screens' validated import, for a page an application ships (`sdk.Import`, contract version 9): the page keeps
// the user where he is and still loads an Excel file through the SCREENS — their columns, their rules per value, their
// lists, their insert and update, the changes captured as from the screen.
//
//   <sdk.Import screens={[{ app: 'nomajde', screen: 'f00950', sheet: 'Security Workbench' }, …]} onClose=… onSaved=… />
//
// One screen: the file's first sheet. Several: a workbook with a sheet per screen, each found by its `sheet` name
// (else the screen's id) — what `sdk.workbook` writes. The dialog is the data screen's own (same modes, same check
// against the rules then the database, same report), each row sent to the screen of its sheet.
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Centered } from '../../common'
import type { Column, QueryResult } from '../../types/connectors'
import type { ScreenDetail } from '../../types/screens'
import { ImportDialog, type ImportMode, type ImportResponse, type RowResult } from '../TableView/ImportDialog'
import { headerMap, loadReturnLookups, mapSheetRows, returnFill, specForRule, validateRow } from '../TableView/importRules'

export interface PageImportScreen { app: string; screen: string; /** the sheet that holds its rows (default: the screen's id) */ sheet?: string }
interface Part { ask: PageImportScreen; detail: ScreenDetail; connector: string; columns: Column[] }

// which screen a row goes to: kept on the row, never sent
const PART = '__part'

export function PageImport({ screens, onClose, onSaved }: {
  screens: PageImportScreen[]
  onClose: () => void
  /** after rows were saved: how many */
  onSaved?: (count: number) => void
}) {
  const { t } = useTranslation()
  const [parts, setParts] = useState<Part[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const asked = JSON.stringify(screens)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const out: Part[] = []
        for (const ask of JSON.parse(asked) as PageImportScreen[]) {
          const detail = await api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(ask.app)}/${encodeURIComponent(ask.screen)}`)
          const connector = detail.connector || ask.app
          // the columns only: what the screen's read says of them (rules, keys, labels)
          const res = await api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(detail.read_query)}?_limit=1&_screen=${encodeURIComponent(ask.screen)}&_app=${encodeURIComponent(ask.app)}`)
          out.push({ ask, detail, connector, columns: res.columns })
        }
        if (!cancelled) setParts(out)
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e))
      }
    }
    void load()
    return () => { cancelled = true }
  }, [asked])

  const parseFile = useCallback(async (file: File): Promise<Record<string, unknown>[] | null> => {
    if (!parts) return null
    const XLSX = await import('xlsx')
    const wb = XLSX.read(await file.arrayBuffer(), { type: 'array' })
    const rows: Record<string, unknown>[] = []
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i]
      const wanted = [p.ask.sheet, p.ask.screen, p.detail.label].filter(Boolean).map((x) => String(x).trim().toLowerCase())
      // Excel cuts a sheet's name at 31 characters
      const name = wb.SheetNames.find((n) => wanted.some((w) => n.trim().toLowerCase() === w.slice(0, 31))) ?? (parts.length === 1 ? wb.SheetNames[0] : undefined)
      const ws = name ? wb.Sheets[name] : undefined
      if (!ws) continue
      const seeds = mapSheetRows(XLSX.utils.sheet_to_json<Record<string, unknown>>(ws, { defval: null }), headerMap(p.columns, t('table.idColumnSuffix')))
      returnFill(p.columns, seeds, await loadReturnLookups(p.columns))
      for (const s of seeds) rows.push({ ...s, [PART]: i })
    }
    return rows.length ? rows : null
  }, [parts, t])

  const partOf = (row: Record<string, unknown>) => (parts ?? [])[Number(row[PART] ?? 0)]
  const bare = (row: Record<string, unknown>) => Object.fromEntries(Object.entries(row).filter(([k]) => k !== PART))

  const check = useCallback((row: Record<string, unknown>) => {
    const p = partOf(row)
    return p ? validateRow(p.columns, bare(row), specForRule, t('import.listUnavailable')) : Promise.resolve([])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parts, t])

  // each row to the screen of its sheet, with the queries that screen has for the mode; the results back in the rows' order
  const send = useCallback(async (commit: boolean, mode: ImportMode, rows: Record<string, unknown>[]): Promise<ImportResponse> => {
    const results: RowResult[] = rows.map((_r, i) => ({ index: i, action: null, ok: false, error: 'not checked' }))
    for (let pi = 0; pi < (parts ?? []).length; pi++) {
      const p = parts![pi]
      const idx = rows.map((r, i) => (Number(r[PART] ?? 0) === pi ? i : -1)).filter((i) => i >= 0)
      if (!idx.length) continue
      const insert = p.detail.insert_query ?? null, update = p.detail.update_query ?? null
      const usable: ImportMode | null = mode === 'upsert' ? (insert && update ? 'upsert' : insert ? 'insert' : update ? 'update' : null)
        : mode === 'insert' ? (insert ? 'insert' : null) : update ? 'update' : null
      if (!usable) { for (const i of idx) results[i] = { index: i, action: null, ok: false, error: t('import.noQueryForMode', { screen: p.detail.label ?? p.ask.screen }) }; continue }
      const resp = await api.post<ImportResponse>(`/api/sql/${encodeURIComponent(p.connector)}/_import`, {
        mode: usable, insert_query: insert, update_query: update, rows: idx.map((i) => bare(rows[i])), commit, screen_app: p.ask.app, screen_id: p.ask.screen,
      })
      resp.results.forEach((r, j) => { results[idx[j]] = { ...r, index: idx[j] } })
    }
    const valid = results.filter((r) => r.ok).length
    return { committed: commit, mode, valid, invalid: results.length - valid, results }
  }, [parts, t])

  const labelOf = useCallback((row: Record<string, unknown>, i: number) => {
    const p = partOf(row)
    if (!p) return `#${i + 1}`
    const keys = p.columns.filter((c) => c.key).map((c) => c.name).filter((k) => row[k] != null && String(row[k]).trim() !== '')
    const where = (parts ?? []).length > 1 ? `${p.ask.sheet ?? p.detail.label ?? p.ask.screen} · ` : ''
    return where + (keys.length ? keys.map((k) => `${k}=${row[k]}`).join(' ') : `#${i + 1}`)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parts])

  if (error) return <Banner $tone="error" style={{ margin: 12 }}>{error}</Banner>
  if (!parts) return <Centered />
  const first = parts[0]
  return (
    <ImportDialog parseFile={parseFile} validateRow={check} keyColumns={[]} connector={first?.connector ?? ''}
      // the modes offered: a mode some screen can do
      insertQuery={parts.find((p) => p.detail.insert_query)?.detail.insert_query ?? null}
      updateQuery={parts.find((p) => p.detail.update_query)?.detail.update_query ?? null}
      send={send} labelOf={labelOf} onClose={onClose} onCommitted={(n) => { onSaved?.(n); onClose() }} />
  )
}
