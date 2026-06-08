// Apply a promotion bundle (exported from another environment) to THIS Liberty — rendered INLINE
// in the Changes page's "Apply" tab (not a modal), mirroring the Package page's Import flow. Pick the
// JSON, dry-run to see the per-op drift report, tick "force" on any conflict you want to override,
// then Apply. Run this on the target (prod) instance.
import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Upload } from 'lucide-react'
import { Button, Banner } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'

type OpSummary = { connector: string; query: string; operation: string; entity: string | null; entity_key: Record<string, unknown> | null; post_apply?: boolean; label?: string | null }
type Result = { index: number; op: OpSummary; status: string; detail?: string; rowcount?: number }
type AlreadyApplied = { name: string; applied_at: string | null; applied_by: string | null }
type Report = { dry_run: boolean; op_count: number; summary: Record<string, number>; results: Result[]; already_applied?: AlreadyApplied | null }
type Bundle = { format?: string; package?: { name?: string; application?: string }; op_count?: number }

const STATUS_TONE: Record<string, string> = {
  would_apply: colors.green.main, applied: colors.green.main, would_force: colors.orange.main,
  conflict: colors.red.main, error: colors.red.main, unverified: colors.orange.main,
}
const Intro = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; margin-bottom: 12px; line-height: 1.5;`
const Drop = styled.button`
  display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%;
  border: 1px dashed ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  padding: 22px; cursor: pointer; margin-bottom: 10px;
  &:hover { border-color: ${colors.blue.border}; color: ${colors.text.secondary}; }
`
const Meta = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; margin: 8px 0;`
const Actions = styled.div`display: flex; gap: 6px; margin: 10px 0;`
const Op = styled.div`
  border: 1px solid ${colors.border}; border-radius: 6px; padding: 7px 10px; margin-bottom: 5px;
  & .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  & .key { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .q { font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; }
  & .detail { font-size: ${fontSize.micro}; color: ${colors.text.secondary}; margin-top: 3px; }
  & label { margin-left: auto; font-size: ${fontSize.micro}; color: ${colors.red.main}; display: inline-flex; gap: 4px; align-items: center; cursor: pointer; }
`
const Badge = styled.span<{ $tone: string }>`
  font-size: ${fontSize.micro}; font-weight: 600; padding: 1px 7px; border-radius: 999px;
  color: ${(p) => p.$tone}; border: 1px solid ${(p) => p.$tone}; text-transform: uppercase; letter-spacing: .03em;
`

export function ApplyBundlePanel({ onApplied }: { onApplied?: () => void }) {
  const { t } = useTranslation()
  const fileRef = useRef<HTMLInputElement>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [bundle, setBundle] = useState<Bundle | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [force, setForce] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setError(null); setReport(null); setForce(new Set()); setFileName(f.name)
    try { setBundle(JSON.parse(await f.text())) }
    catch { setError(t('settings.changes.badBundle', 'Not a valid JSON bundle.')); setBundle(null) }
  }

  const run = async (dry: boolean) => {
    if (!bundle) return
    setBusy(true); setError(null)
    try {
      const r = await api.post<Report>('/admin/changesets/apply', { bundle, dry_run: dry, force: [...force] })
      setReport(r)
      if (!dry) onApplied?.()   // refresh the import log after a real apply
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const toggleForce = (i: number) => setForce((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n })
  const summaryText = useMemo(() => report ? Object.entries(report.summary).map(([k, v]) => `${v} ${k}`).join(' · ') : '', [report])
  const hasConflicts = (report?.summary.conflict ?? 0) > 0

  return (
    <div>
      <Intro>{t('settings.changes.applyIntro',
        'Apply a promotion bundle exported from another install (or this one) to THIS environment. Dry-run first to see the per-op drift report; tick “force” on any conflict you want to override.')}</Intro>
      <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: 'none' }} onChange={(e) => void onFile(e)} />
      <Drop type="button" onClick={() => fileRef.current?.click()}>
        <Upload size={15} /> {fileName ?? t('settings.changes.pickBundle', 'Click to pick a bundle.json')}
      </Drop>
      {bundle && (
        <Meta>{bundle.package?.name} · {t('settings.changes.opsCount', '{{n}} op(s)', { n: bundle.op_count ?? 0 })} · {bundle.package?.application}</Meta>
      )}
      <Actions>
        <Button $size="sm" $variant="ghost" disabled={!bundle || busy} onClick={() => void run(true)}>
          {t('settings.changes.dryRun', 'Dry run')}
        </Button>
        <Button $size="sm" $variant="primary" disabled={!bundle || busy || !report} onClick={() => void run(false)}>
          {hasConflicts && force.size === 0 ? t('settings.changes.applyAnyway', 'Apply (skip conflicts)') : t('settings.changes.apply', 'Apply')}
        </Button>
      </Actions>
      {error && <Banner $tone="error" style={{ marginTop: 8 }}>{error}</Banner>}
      {report?.already_applied && (
        <Banner $tone="warning" style={{ marginTop: 8 }}>
          {t('settings.changes.alreadyApplied',
            'This bundle was already applied on {{date}}{{by}}.',
            {
              date: report.already_applied.applied_at ? new Date(report.already_applied.applied_at).toLocaleString() : '?',
              by: report.already_applied.applied_by ? ` by ${report.already_applied.applied_by}` : '',
            })}
        </Banner>
      )}
      {report && (
        <>
          <Banner $tone={report.summary.conflict || report.summary.error ? 'warning' : 'ok'} style={{ margin: '8px 0' }}>
            {report.dry_run ? t('settings.changes.dryRunReport', 'Dry run — nothing written. ') : t('settings.changes.applied', 'Applied. ')}{summaryText}
          </Banner>
          {report.results.map((r) => (
            <Op key={r.index}>
              <div className="top">
                <Badge $tone={STATUS_TONE[r.status] ?? colors.text.muted}>{r.status.replace('_', ' ')}</Badge>
                {r.op.post_apply && <Badge $tone={colors.blue.main}>{t('settings.changes.postApply', 'post-apply')}</Badge>}
                <span className="key">{r.op.post_apply ? (r.op.label || r.op.operation) : `${r.op.operation} ${r.op.entity_key ? Object.entries(r.op.entity_key).map(([k, v]) => `${k}=${v}`).join(' · ') : ''}`}</span>
                <span className="q">{r.op.connector}.{r.op.query}</span>
                {r.status === 'conflict' && report.dry_run && (
                  <label><input type="checkbox" checked={force.has(r.index)} onChange={() => toggleForce(r.index)} /> {t('settings.changes.force', 'force')}</label>
                )}
              </div>
              {r.detail && <div className="detail">{r.detail}</div>}
            </Op>
          ))}
        </>
      )}
    </div>
  )
}

export default ApplyBundlePanel
