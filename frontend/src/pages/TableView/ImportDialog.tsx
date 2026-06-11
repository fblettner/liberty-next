// Validated Excel import. The dialog opens FIRST (empty); the operator drops / picks a file, chooses
// how existing rows are handled (upsert / insert-only / update-only), then clicks Check. Check runs
// TWO passes with a progress bar:
//   1. Screen rules (client-side) — every cell against its column's rule: a LOOKUP value must be a
//      real code, an ENUM value must be allowed, a BOOLEAN must be the true/false token. The DB can't
//      catch these (a JDE CHAR column accepts any junk), so we check them here first.
//   2. DB dry-run (server) — the rows that passed (1) run the real insert/update inside a transaction
//      that's rolled back, so constraint violations (PK/unique where enforced, FK, NOT NULL, type)
//      and — in upsert/insert — an already-existing key surface exactly as a real save would.
// The operator sees a per-row report, then "Save valid rows" writes ONLY the passing rows (chunked,
// with progress).
import { createPortal } from 'react-dom'
import { useCallback, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Upload } from 'lucide-react'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Banner } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts } from '../../theme'

type ImportMode = 'upsert' | 'insert' | 'update'

interface RowResult { index: number; action: string | null; ok: boolean; error?: string; rowcount?: number }
interface ImportResponse { committed: boolean; mode: string; valid: number; invalid: number; results: RowResult[] }

const CHUNK = 50   // rows per server round-trip — keeps each request small + drives the progress bar

const Label = styled.div`font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; margin-bottom: 6px;`
const ModeRow = styled.div`display: flex; gap: 6px; align-items: center; margin-bottom: 14px; flex-wrap: wrap;`
const ModeBtn = styled.button<{ $active: boolean }>`
  padding: 4px 12px; border-radius: 999px; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  border: 1px solid ${(p) => (p.$active ? colors.blue.main : colors.border)};
  background: ${(p) => (p.$active ? colors.blue.bg : 'transparent')};
  color: ${(p) => (p.$active ? colors.blue.main : colors.text.secondary)};
  &:hover { border-color: ${colors.blue.main}; }
`
const Drop = styled.div<{ $over: boolean }>`
  display: flex; flex-direction: column; align-items: center; gap: 8px; justify-content: center;
  padding: 26px 16px; border-radius: 12px; cursor: pointer; text-align: center;
  border: 1.5px dashed ${(p) => (p.$over ? colors.blue.main : colors.border)};
  background: ${(p) => (p.$over ? colors.blue.bg : colors.bg.input)};
  color: ${colors.text.secondary}; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  transition: border-color 0.12s, background 0.12s;
  .file { color: ${colors.text.primary}; font-weight: 600; }
`
const Progress = styled.div`margin: 14px 0 6px;`
const Bar = styled.div`height: 6px; border-radius: 999px; background: ${colors.bg.input}; overflow: hidden;`
const Fill = styled.div<{ $pct: number }>`height: 100%; width: ${(p) => p.$pct}%; background: ${colors.blue.main}; transition: width 0.15s;`
const ProgLabel = styled.div`font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.secondary}; margin-top: 6px;`
const Summary = styled.div`
  display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin: 12px 0 10px;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm};
`
const Pill = styled.span<{ $tone: 'ok' | 'bad' }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 2px 10px; border-radius: 999px; font-weight: 600;
  color: ${(p) => (p.$tone === 'ok' ? colors.green.main : colors.red.main)};
  background: ${(p) => (p.$tone === 'ok' ? colors.green.bg : colors.red.bg)};
`
const ErrList = styled.div`max-height: 38vh; overflow: auto; display: flex; flex-direction: column; gap: 8px;`
const ErrItem = styled.div`border: 1px solid ${colors.red.border}; border-radius: 8px; padding: 8px 10px; background: ${colors.red.bg};`
const ErrHead = styled.div`
  display: flex; gap: 10px; align-items: baseline; justify-content: space-between; margin-bottom: 4px;
  font-family: ${fonts.mono}; font-size: ${fontSize.micro};
  .label { color: ${colors.text.primary}; word-break: break-word; }
  .action { color: ${colors.text.secondary}; text-transform: uppercase; flex-shrink: 0; }
`
const ErrMsg = styled.div`
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.red.main};
  white-space: pre-wrap; word-break: break-word; line-height: 1.45;
`

export function ImportDialog({
  parseFile, validateRow, keyColumns, connector, insertQuery, updateQuery, screenApp, screenId, onClose, onCommitted,
}: {
  /** Parse + header-map a chosen file into rows keyed by column name (with lookup return-fill). */
  parseFile: (file: File) => Promise<Record<string, unknown>[] | null>
  /** Screen-rule check for one mapped row → human-readable errors (empty = passes). Reuses the grid's
   *  own rule resolution, so it's rules_when-aware + trim-tolerant. */
  validateRow: (row: Record<string, unknown>) => string[]
  /** Key columns — used to label each report row so the operator can find it in the sheet. */
  keyColumns: string[]
  connector: string
  insertQuery?: string | null
  updateQuery?: string | null
  screenApp?: string | null
  screenId?: string | null
  onClose: () => void
  onCommitted: (committed: number) => void
}) {
  const { t } = useTranslation()
  const defaultMode: ImportMode = insertQuery && updateQuery ? 'upsert' : insertQuery ? 'insert' : 'update'
  const [mode, setMode] = useState<ImportMode>(defaultMode)
  const [file, setFile] = useState<File | null>(null)
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null)
  const [phase, setPhase] = useState<'pick' | 'checking' | 'committing'>('pick')
  const [progress, setProgress] = useState(0)            // 0..1
  const [progressLabel, setProgressLabel] = useState('')
  const [report, setReport] = useState<ImportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [committedCount, setCommittedCount] = useState<number | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const busy = phase === 'checking' || phase === 'committing'

  const resetReport = useCallback(() => { setReport(null); setCommittedCount(null); setError(null) }, [])

  const takeFile = useCallback(async (f: File) => {
    resetReport(); setFile(f); setRows(null)
    const parsed = await parseFile(f)
    if (!parsed || parsed.length === 0) { setError(t('import.noRows', 'No rows matched this screen’s columns.')); setFile(null); return }
    setRows(parsed)
  }, [parseFile, resetReport, t])

  const pickMode = useCallback((m: ImportMode) => { setMode(m); resetReport() }, [resetReport])

  const post = useCallback(
    (commit: boolean, sendRows: Record<string, unknown>[]) =>
      api.post<ImportResponse>(`/api/sql/${encodeURIComponent(connector)}/_import`, {
        mode, insert_query: insertQuery ?? null, update_query: updateQuery ?? null,
        rows: sendRows, commit, screen_app: screenApp ?? null, screen_id: screenId ?? null,
      }),
    [connector, mode, insertQuery, updateQuery, screenApp, screenId],
  )

  const check = useCallback(async () => {
    if (!rows) return
    setPhase('checking'); setError(null); setReport(null); setCommittedCount(null); setProgress(0)
    try {
      // Pass 1 — screen rules (client-side, instant). Reuses the grid's own per-row resolution so
      // bad lookup/enum/boolean values are caught (the DB can't tell — JDE CHAR accepts anything).
      setProgressLabel(t('import.checkingRules', 'Checking screen rules…'))
      const ruleErr = new Map<number, string>()
      for (let i = 0; i < rows.length; i++) {
        const e = validateRow(rows[i])
        if (e.length) ruleErr.set(i, e.join(' · '))
      }
      setProgress(0.3)
      // Pass 2 — DB dry-run for the rows that passed the rule check (the real save path, rolled back).
      setProgressLabel(t('import.checkingDb', 'Checking against the database…'))
      const dbIdx = rows.map((_, i) => i).filter((i) => !ruleErr.has(i))
      const dbRes = new Map<number, RowResult>()
      for (let s = 0; s < dbIdx.length; s += CHUNK) {
        const batchIdx = dbIdx.slice(s, s + CHUNK)
        const resp = await post(false, batchIdx.map((i) => rows[i]))
        resp.results.forEach((r, j) => dbRes.set(batchIdx[j], r))
        setProgress(0.3 + (Math.min(s + CHUNK, dbIdx.length) / Math.max(1, dbIdx.length)) * 0.7)
      }
      const merged: RowResult[] = rows.map((_, i) =>
        ruleErr.has(i)
          ? { index: i, action: null, ok: false, error: ruleErr.get(i)! }
          : (dbRes.get(i) ?? { index: i, action: null, ok: false, error: 'not checked' }))
      const valid = merged.filter((r) => r.ok).length
      setReport({ committed: false, mode, valid, invalid: merged.length - valid, results: merged })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setPhase('pick'); setProgress(0); setProgressLabel('')
    }
  }, [rows, validateRow, post, mode, t])

  const commit = useCallback(async () => {
    if (!report || !rows) return
    const validIdx = report.results.filter((r) => r.ok).map((r) => r.index)
    if (validIdx.length === 0) return
    setPhase('committing'); setError(null); setProgress(0); setProgressLabel(t('import.saving', 'Saving…'))
    try {
      let savedOk = 0
      const failed: RowResult[] = []
      for (let s = 0; s < validIdx.length; s += CHUNK) {
        const batchIdx = validIdx.slice(s, s + CHUNK)
        const resp = await post(true, batchIdx.map((i) => rows[i]))
        resp.results.forEach((r, j) => (r.ok ? (savedOk += 1) : failed.push({ ...r, index: batchIdx[j] })))
        setProgress(Math.min(s + CHUNK, validIdx.length) / validIdx.length)
      }
      if (failed.length === 0) { onCommitted(savedOk); return }
      setReport({ committed: true, mode, valid: savedOk, invalid: failed.length, results: failed })
      setCommittedCount(savedOk)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setPhase('pick'); setProgress(0); setProgressLabel('')
    }
  }, [report, rows, post, onCommitted, mode, t])

  const modeOpts = useMemo(() => {
    const o: { value: ImportMode; label: string }[] = []
    if (insertQuery && updateQuery) o.push({ value: 'upsert', label: t('import.modeUpsert', 'Update or insert') })
    if (insertQuery) o.push({ value: 'insert', label: t('import.modeInsert', 'Insert only') })
    if (updateQuery) o.push({ value: 'update', label: t('import.modeUpdate', 'Update only') })
    return o
  }, [insertQuery, updateQuery, t])

  const rowLabel = (i: number) => {
    const r = rows?.[i] ?? {}
    const keys = keyColumns.filter((k) => r[k] != null && String(r[k]).trim() !== '')
    return keys.length ? keys.map((k) => `${k}=${r[k]}`).join(' ') : `#${i + 1}`
  }

  const validCount = report?.results.filter((r) => r.ok).length ?? 0
  const invalidResults = report?.results.filter((r) => !r.ok) ?? []
  const pct = Math.round(progress * 100)

  return createPortal(
    <Overlay onClick={onClose} style={{ zIndex: 2000 }}>
      <Modal style={{ width: 720, maxWidth: '94vw' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('import.title', 'Import from Excel')}</ModalHeader>
        <ModalBody>
          {modeOpts.length > 1 && (
            <>
              <Label>{t('import.mode', 'Existing rows')}</Label>
              <ModeRow>
                {modeOpts.map((m) => (
                  <ModeBtn key={m.value} $active={mode === m.value} onClick={() => pickMode(m.value)} disabled={busy}>
                    {m.label}
                  </ModeBtn>
                ))}
              </ModeRow>
            </>
          )}

          <Drop
            $over={dragOver}
            onClick={() => !busy && fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); if (!busy) setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); if (busy) return; const f = e.dataTransfer.files?.[0]; if (f) void takeFile(f) }}
          >
            <Upload size={20} />
            {file
              ? <span><span className="file">{file.name}</span>{rows ? ` · ${t('import.total', { count: rows.length, defaultValue: '{{count}} rows' })}` : ''}</span>
              : <span>{t('import.drop', 'Drop a .xlsx / .csv here, or click to browse')}</span>}
            <input
              ref={fileRef} type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void takeFile(f); e.target.value = '' }}
            />
          </Drop>

          {error && <Banner $tone="error">{error}</Banner>}
          {committedCount != null && <Banner $tone="ok">{t('import.committedSome', { count: committedCount, defaultValue: '{{count}} rows saved.' })}</Banner>}

          {busy && (
            <Progress>
              <Bar><Fill $pct={pct} /></Bar>
              <ProgLabel>{progressLabel} {pct}%</ProgLabel>
            </Progress>
          )}

          {report && !busy && (
            <>
              <Summary>
                <span style={{ color: colors.text.secondary }}>{t('import.total', { count: report.results.length, defaultValue: '{{count}} rows' })}</span>
                <Pill $tone="ok">✓ {t('import.valid', { count: validCount, defaultValue: '{{count}} valid' })}</Pill>
                {report.invalid > 0 && <Pill $tone="bad">✕ {t('import.invalid', { count: report.invalid, defaultValue: '{{count}} invalid' })}</Pill>}
              </Summary>
              {invalidResults.length > 0 && (
                <ErrList>
                  {invalidResults.map((r) => (
                    <ErrItem key={r.index}>
                      <ErrHead>
                        <span className="label"><b>{t('import.rowNum', { n: r.index + 1, defaultValue: 'Row {{n}}' })}</b> · {rowLabel(r.index)}</span>
                        <span className="action">{r.action ?? t('import.ruleError', 'rule')}</span>
                      </ErrHead>
                      <ErrMsg>{r.error}</ErrMsg>
                    </ErrItem>
                  ))}
                </ErrList>
              )}
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button
            $size="sm"
            $variant="ghost"
            onClick={() => (committedCount != null ? onCommitted(committedCount) : onClose())}
            disabled={busy}
          >
            {committedCount != null ? t('common.close', 'Close') : t('common.cancel')}
          </Button>
          {report && committedCount == null ? (
            <Button $size="sm" $variant="primary" onClick={commit} disabled={busy || validCount === 0}>
              {t('import.saveValid', { count: validCount, defaultValue: 'Save {{count}} valid rows' })}
            </Button>
          ) : (
            <Button $size="sm" $variant="primary" onClick={check} disabled={busy || !rows || committedCount != null}>
              {t('import.check', 'Check')}
            </Button>
          )}
        </ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}
