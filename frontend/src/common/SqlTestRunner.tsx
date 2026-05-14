// An inline collapsible panel below each SqlEditor — POSTs the current SQL to
// /admin/config/connectors/{c}/test-sql and shows the result. SELECT → a small DataTable
// (height-capped); writes → a rowcount + a "Commit" button that re-runs without dry_run.
// Params are auto-extracted from `:name` placeholders in the SQL so the operator gets a form
// for them without typing JSON.
//
// NOT re-exported from `common/index.ts` — direct import only (rides the Settings chunk).
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Play, X, CheckCircle, AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from './Button'
import { Input } from './Input'
import { SpinnerRing } from './Spinner'
import { Banner } from './Banner'
import { Mono } from './Tag'
import { api, ApiError } from '../api/client'
import { colors, fontSize, fonts, radius } from '../theme'

// Mirror of QueryResult.to_dict() from liberty/connectors/sql.py — duplicated here rather
// than barreled in to keep this file self-contained.
interface TestRunResult {
  statement_type: string
  columns: Array<{ name: string; type?: string | null }>
  rows: Array<Record<string, unknown>>
  row_count: number
  rowcount: number
  truncated: boolean
  duration_ms: number
}

const Panel = styled.div`
  margin-top: 6px; padding: 8px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input}; display: flex; flex-direction: column; gap: 8px;
`
const PanelHead = styled.div`display: flex; align-items: center; gap: 8px; font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const ParamRow = styled.div`display: flex; gap: 6px; align-items: center;`
const ParamName = styled.span`font-family: ${fonts.mono}; color: ${colors.text.muted}; font-size: ${fontSize.sm}; flex: 0 0 160px;`
const Status = styled.div<{ $ok?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; font-size: ${fontSize.sm};
  color: ${({ $ok }) => ($ok ? colors.green.main : colors.red.main)};
`
const TableWrap = styled.div`
  overflow: auto; max-height: 260px; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  background: var(--bg);
`
const Table = styled.table`
  border-collapse: collapse; width: 100%; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  & th, & td {
    padding: 4px 8px; border-bottom: 1px solid ${colors.border}; text-align: left;
    white-space: nowrap; color: ${colors.text.secondary};
  }
  & th { background: ${colors.bg.input}; color: ${colors.text.muted}; text-transform: uppercase;
    font-size: ${fontSize.micro}; letter-spacing: 0.04em; position: sticky; top: 0; }
  & tr:hover td { background: ${colors.bg.input}; }
`

export interface SqlTestRunnerProps {
  connector: string
  sql: string
  onClose: () => void
}

const BIND_RE = /(?<!:):([A-Za-z_]\w*)(?!\w)/g

/** Pick out :param tokens from the SQL, ignoring `::cast`. Same regex shape the backend uses. */
function extractBinds(sql: string): string[] {
  const seen = new Set<string>()
  let m
  while ((m = BIND_RE.exec(sql)) !== null) seen.add(m[1])
  return Array.from(seen)
}

export function SqlTestRunner({ connector, sql, onClose }: SqlTestRunnerProps) {
  const { t } = useTranslation()
  const binds = useMemo(() => extractBinds(sql), [sql])
  const [params, setParams] = useState<Record<string, string>>({})
  const [maxRows, setMaxRows] = useState('100')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<TestRunResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Drop params whose binds disappear when the SQL changes.
  useEffect(() => {
    setParams((cur) => {
      const next: Record<string, string> = {}
      for (const b of binds) if (b in cur) next[b] = cur[b]
      return next
    })
  }, [binds.join('|')])

  async function run(dryRun: boolean) {
    setBusy(true); setError(null)
    try {
      const cleanParams: Record<string, string | null> = {}
      for (const b of binds) cleanParams[b] = params[b] === '' || params[b] === undefined ? null : params[b]
      const body = {
        sql, params: cleanParams,
        max_rows: maxRows.trim() ? parseInt(maxRows, 10) : null,
        dry_run: dryRun,
      }
      const r = await api.post<TestRunResult>(`/admin/config/connectors/${encodeURIComponent(connector)}/test-sql`, body)
      setResult(r)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const isWrite = result && result.statement_type !== 'SELECT'

  return (
    <Panel>
      <PanelHead>
        <Play size={13} />
        <strong>{t('settings.sqlRunner.title')}</strong>
        <span style={{ flex: 1, color: colors.text.muted }}>{t('settings.sqlRunner.dryRunNote')}</span>
        <button type="button" onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: colors.text.muted, cursor: 'pointer', display: 'inline-flex', alignItems: 'center' }}>
          <X size={14} />
        </button>
      </PanelHead>

      {binds.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {binds.map((b) => (
            <ParamRow key={b}>
              <ParamName>:{b}</ParamName>
              <Input style={{ flex: 1 }} placeholder={t('settings.sqlRunner.bindBlank')}
                value={params[b] ?? ''} onChange={(e) => setParams({ ...params, [b]: e.target.value })} />
            </ParamRow>
          ))}
        </div>
      )}

      <ParamRow>
        <ParamName>max_rows</ParamName>
        <Input style={{ flex: '0 0 120px' }} value={maxRows} onChange={(e) => setMaxRows(e.target.value)} placeholder="100" />
        <Button $size="sm" $variant="primary" onClick={() => run(true)} disabled={busy || !sql.trim()}>
          {busy ? <SpinnerRing size={12} thickness={2} /> : <Play size={12} />} {t('settings.sqlRunner.run')}
        </Button>
        {isWrite && (
          <Button $size="sm" $variant="danger" onClick={() => run(false)} disabled={busy} title={t('settings.sqlRunner.commitTitle')}>
            <CheckCircle size={12} /> {t('settings.sqlRunner.commit')}
          </Button>
        )}
      </ParamRow>

      {error && <Banner $tone="error"><AlertTriangle size={14} /> {error}</Banner>}

      {result && !error && (
        <>
          <Status $ok>
            <CheckCircle size={13} />
            <span>
              {t('settings.sqlRunner.stmt', { type: result.statement_type })} ·
              {result.statement_type === 'SELECT'
                ? ` ${result.row_count} ${t('settings.sqlRunner.rows')}` + (result.truncated ? ` (${t('settings.sqlRunner.truncated')})` : '')
                : ` ${result.rowcount} ${t('settings.sqlRunner.affected')}`}
              · {result.duration_ms.toFixed(1)} ms
            </span>
          </Status>
          {result.statement_type === 'SELECT' && result.rows.length > 0 && (
            <TableWrap>
              <Table>
                <thead>
                  <tr>{result.columns.map((c) => <th key={c.name} title={c.type ?? undefined}>{c.name}</th>)}</tr>
                </thead>
                <tbody>
                  {result.rows.map((row, i) => (
                    <tr key={i}>{result.columns.map((c) => <td key={c.name}><Mono>{formatCell(row[c.name])}</Mono></td>)}</tr>
                  ))}
                </tbody>
              </Table>
            </TableWrap>
          )}
        </>
      )}
    </Panel>
  )
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return '∅'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
