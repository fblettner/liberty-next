// Apply a promotion bundle (exported from another environment) to THIS Liberty. Upload the JSON,
// dry-run to see the per-op drift report, tick "force" on any conflict you want to override, then
// Apply. Run this on the target (prod) instance.
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Banner } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts } from '../../theme'

type OpSummary = { connector: string; query: string; operation: string; entity: string | null; entity_key: Record<string, unknown> | null }
type Result = { index: number; op: OpSummary; status: string; detail?: string; rowcount?: number }
type Report = { dry_run: boolean; op_count: number; summary: Record<string, number>; results: Result[] }
type Bundle = { format?: string; package?: { name?: string; application?: string }; op_count?: number }

const STATUS_TONE: Record<string, string> = {
  would_apply: colors.green.main, applied: colors.green.main, would_force: colors.orange.main,
  conflict: colors.red.main, error: colors.red.main, unverified: colors.orange.main,
}
const Meta = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; margin: 8px 0;`
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

export function ApplyBundleModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const [bundle, setBundle] = useState<Bundle | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [force, setForce] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setError(null); setReport(null); setForce(new Set())
    try { setBundle(JSON.parse(await f.text())) }
    catch { setError(t('settings.changes.badBundle', 'Not a valid JSON bundle.')); setBundle(null) }
  }

  const run = async (dry: boolean) => {
    if (!bundle) return
    setBusy(true); setError(null)
    try {
      setReport(await api.post<Report>('/admin/changesets/apply', { bundle, dry_run: dry, force: [...force] }))
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const toggleForce = (i: number) => setForce((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n })
  const summaryText = useMemo(() => report ? Object.entries(report.summary).map(([k, v]) => `${v} ${k}`).join(' · ') : '', [report])
  const hasConflicts = (report?.summary.conflict ?? 0) > 0

  return (
    <Overlay>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(760px, 95vw)', maxHeight: '88vh', display: 'flex', flexDirection: 'column' }}>
        <ModalHeader>{t('settings.changes.applyTitle', 'Apply a promotion bundle')}</ModalHeader>
        <ModalBody style={{ overflow: 'auto' }}>
          <input type="file" accept=".json,application/json" onChange={(e) => void onFile(e)} />
          {bundle && (
            <Meta>{bundle.package?.name} · {bundle.op_count ?? 0} {t('settings.changes.nOps', 'op(s)')} · {bundle.package?.application}</Meta>
          )}
          {error && <Banner $tone="error" style={{ marginTop: 8 }}>{error}</Banner>}
          {report && (
            <>
              <Banner $tone={report.summary.conflict || report.summary.error ? 'warning' : 'ok'} style={{ margin: '8px 0' }}>
                {report.dry_run ? t('settings.changes.dryRunReport', 'Dry run — nothing written. ') : t('settings.changes.applied', 'Applied. ')}{summaryText}
              </Banner>
              {report.results.map((r) => (
                <Op key={r.index}>
                  <div className="top">
                    <Badge $tone={STATUS_TONE[r.status] ?? colors.text.muted}>{r.status.replace('_', ' ')}</Badge>
                    <span className="key">{r.op.operation} {r.op.entity_key ? Object.entries(r.op.entity_key).map(([k, v]) => `${k}=${v}`).join(' · ') : ''}</span>
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
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.close', 'Close')}</Button>
          <Button $size="sm" $variant="ghost" disabled={!bundle || busy} onClick={() => void run(true)}>
            {t('settings.changes.dryRun', 'Dry run')}
          </Button>
          <Button $size="sm" $variant="primary" disabled={!bundle || busy || !report} onClick={() => void run(false)}>
            {hasConflicts && force.size === 0 ? t('settings.changes.applyAnyway', 'Apply (skip conflicts)') : t('settings.changes.apply', 'Apply')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export default ApplyBundleModal
