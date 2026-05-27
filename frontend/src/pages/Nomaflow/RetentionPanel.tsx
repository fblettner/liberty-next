// Retention panel — surfaces nomaflow's run-history cleanup policy + the last
// sweep's result on the Jobs list page, plus an inline editor + "Clean now"
// button. The policy lives on [meta.retention] in jobs.toml so changes round-trip
// through GET/PUT /admin/config/jobs/parsed + reload (same flow JobsList uses
// for the per-job enable toggle).
//
// Collapsed by default to keep the Jobs list visually dominant — operators only
// expand when they want to inspect / change the policy or kick a manual sweep.
import { useCallback, useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Archive, ChevronDown, ChevronRight, Save, Trash2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Input, Mono, SpinnerRing } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

interface RetentionPolicy {
  enabled: boolean
  days: number
  keep_last_per_job: number
  sweep_interval_minutes: number
}

interface SweepReport {
  swept_at: string
  cutoff: string
  keep_last_per_job: number
  deleted_by_age: number
  deleted_by_cap: number
  total_deleted: number
}

interface RetentionStatus {
  policy: RetentionPolicy
  last_sweep: SweepReport | null
}

interface JobsParsed {
  meta?: { version?: number; retention?: Partial<RetentionPolicy> }
  jobs: unknown[]
}

const Panel = styled(Card)`
  display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px;
  padding: 10px 14px;
`
const Header = styled.button`
  font-family: inherit; background: transparent; border: 0; padding: 0;
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  color: ${colors.text.primary}; font-size: ${fontSize.sm};
  &:hover { color: ${colors.blue.main}; }
`
const HeaderLabel = styled.span`font-weight: 600;`
const HeaderSummary = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};`
const HeaderSpacer = styled.span`flex: 1;`
// Last-sweep pill — small + muted, sits next to the header so the operator sees
// "what happened" at a glance without expanding.
const SweepPill = styled.span<{ $tone: 'neutral' | 'green' }>`
  font-family: ${fonts.mono}; font-size: ${fontSize.micro};
  padding: 1px 6px; border-radius: ${radius.sm};
  background: ${({ $tone }) => $tone === 'green' ? colors.green.bg : 'transparent'};
  color: ${({ $tone }) => $tone === 'green' ? colors.green.main : colors.text.muted};
  border: 1px solid ${({ $tone }) => $tone === 'green' ? colors.green.border : colors.border};
`
const Body = styled.div`
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px; align-items: end; margin-top: 4px;
`
const Field = styled.label`
  display: flex; flex-direction: column; gap: 4px;
  font-size: ${fontSize.sm}; color: ${colors.text.secondary};
`
const FieldLabel = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em;`
const Toggle = styled.button<{ $on: boolean }>`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  height: 32px; padding: 0 12px; border-radius: ${radius.sm}; cursor: pointer;
  border: 1px solid ${({ $on }) => $on ? colors.green.border : colors.border};
  background: ${({ $on }) => $on ? colors.green.bg : 'transparent'};
  color: ${({ $on }) => $on ? colors.green.main : colors.text.muted};
`
const Actions = styled.div`
  display: flex; gap: 8px; margin-top: 10px; align-items: center; flex-wrap: wrap;
`

export interface RetentionPanelProps {
  /** Called after a successful Save or sweep so the parent Jobs list can refresh
   *  (a sweep may have deleted the latest run from a job's display). */
  onChanged?: () => void
}

export function RetentionPanel({ onChanged }: RetentionPanelProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<RetentionStatus | null>(null)
  const [draft, setDraft] = useState<RetentionPolicy | null>(null)
  const [busy, setBusy] = useState<'load' | 'save' | 'sweep' | null>('load')
  const [error, setError] = useState<string | null>(null)
  // Compact toast for sweep results — shown next to the buttons rather than as a
  // page-wide banner so the operator's eye stays in the panel.
  const [toast, setToast] = useState<string | null>(null)

  const load = useCallback(async () => {
    setBusy('load')
    setError(null)
    try {
      const r = await api.get<RetentionStatus>('/admin/jobs/retention')
      setStatus(r)
      setDraft(r.policy)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  /** Save — round-trips through GET/PUT /admin/config/jobs/parsed so the [meta.retention]
   *  block lands in jobs.toml; then triggers a reload so the scheduler picks up the new
   *  policy (re-registers the sweep at the new interval, etc.) without restarting. */
  const onSave = useCallback(async () => {
    if (!draft) return
    setBusy('save')
    setError(null)
    try {
      const parsed = await api.get<JobsParsed>('/admin/config/jobs/parsed')
      const meta = { ...(parsed.meta ?? {}), retention: draft }
      await api.put('/admin/config/jobs/parsed', { ...parsed, meta })
      await api.post('/admin/reload')
      await load()
      onChanged?.()
      setToast(t('nomaflow.retention.saved', 'Policy saved + scheduler reloaded.'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }, [draft, load, onChanged, t])

  const onSweep = useCallback(async () => {
    setBusy('sweep')
    setError(null)
    try {
      const r = await api.post<SweepReport>('/admin/jobs/retention/sweep')
      setStatus((s) => s ? { ...s, last_sweep: r } : s)
      setToast(t(
        'nomaflow.retention.swept',
        `Deleted ${r.total_deleted} run${r.total_deleted === 1 ? '' : 's'} (age: ${r.deleted_by_age}, cap: ${r.deleted_by_cap}).`,
        { total: r.total_deleted, age: r.deleted_by_age, cap: r.deleted_by_cap },
      ))
      onChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }, [onChanged, t])

  const headerSummary = status
    ? status.policy.enabled
      ? t('nomaflow.retention.summary',
          `${status.policy.days}d · cap ${status.policy.keep_last_per_job}/job · every ${status.policy.sweep_interval_minutes} min`,
          { days: status.policy.days, cap: status.policy.keep_last_per_job, interval: status.policy.sweep_interval_minutes })
      : t('nomaflow.retention.disabled', 'Auto-sweep disabled')
    : '…'

  const lastSweepPill = status?.last_sweep ? (
    <SweepPill $tone={status.last_sweep.total_deleted > 0 ? 'green' : 'neutral'}>
      {t('nomaflow.retention.lastSwept', `last swept ${formatRelative(status.last_sweep.swept_at)} · ${status.last_sweep.total_deleted} deleted`,
        { when: formatRelative(status.last_sweep.swept_at), deleted: status.last_sweep.total_deleted })}
    </SweepPill>
  ) : null

  return (
    <Panel>
      <Header type="button" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Archive size={14} />
        <HeaderLabel>{t('nomaflow.retention.title', 'Run history retention')}</HeaderLabel>
        <HeaderSummary>· {headerSummary}</HeaderSummary>
        <HeaderSpacer />
        {lastSweepPill}
      </Header>
      {open && (
        <>
          {error && <Banner $tone="error">{error}</Banner>}
          {!status && busy === 'load' && (
            <span style={{ color: colors.text.muted, fontSize: fontSize.sm, paddingTop: 6 }}>
              <SpinnerRing size={12} thickness={2} /> {t('common.loading')}
            </span>
          )}
          {draft && (
            <>
              <Body>
                <Field>
                  <FieldLabel>{t('nomaflow.retention.enabledLabel', 'Auto-sweep')}</FieldLabel>
                  <Toggle type="button" $on={draft.enabled}
                    onClick={() => setDraft({ ...draft, enabled: !draft.enabled })}>
                    {draft.enabled ? t('nomaflow.retention.on', 'On') : t('nomaflow.retention.off', 'Off')}
                  </Toggle>
                </Field>
                <Field>
                  <FieldLabel>{t('nomaflow.retention.daysLabel', 'Days kept')}</FieldLabel>
                  <Input type="number" min={1} max={3650} value={draft.days}
                    onChange={(e) => setDraft({ ...draft, days: clamp(parseInt(e.target.value || '0', 10), 1, 3650) })} />
                </Field>
                <Field>
                  <FieldLabel>{t('nomaflow.retention.capLabel', 'Cap per job')}</FieldLabel>
                  <Input type="number" min={1} max={100000} value={draft.keep_last_per_job}
                    onChange={(e) => setDraft({ ...draft, keep_last_per_job: clamp(parseInt(e.target.value || '0', 10), 1, 100000) })} />
                </Field>
                <Field>
                  <FieldLabel>{t('nomaflow.retention.intervalLabel', 'Sweep interval (min)')}</FieldLabel>
                  <Input type="number" min={5} max={1440} value={draft.sweep_interval_minutes}
                    onChange={(e) => setDraft({ ...draft, sweep_interval_minutes: clamp(parseInt(e.target.value || '0', 10), 5, 1440) })} />
                </Field>
              </Body>
              <Actions>
                <Button $variant="primary" $size="sm" onClick={onSave}
                  disabled={busy !== null || !policyChanged(draft, status?.policy)}>
                  {busy === 'save' ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />}
                  {t('common.save')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={onSweep} disabled={busy !== null}>
                  {busy === 'sweep' ? <SpinnerRing size={13} thickness={2} /> : <Trash2 size={13} />}
                  {t('nomaflow.retention.sweepNow', 'Clean now')}
                </Button>
                {toast && (
                  <Mono style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{toast}</Mono>
                )}
              </Actions>
            </>
          )}
        </>
      )}
    </Panel>
  )
}

// ── helpers ───────────────────────────────────────────────────────────────────

function clamp(v: number, min: number, max: number): number {
  if (!Number.isFinite(v)) return min
  return Math.min(Math.max(v, min), max)
}

function policyChanged(draft: RetentionPolicy, current: RetentionPolicy | undefined): boolean {
  if (!current) return false
  return draft.enabled !== current.enabled
    || draft.days !== current.days
    || draft.keep_last_per_job !== current.keep_last_per_job
    || draft.sweep_interval_minutes !== current.sweep_interval_minutes
}

/** Compact "5 min ago" / "2 h ago" / "Apr 12" — enough resolution to know whether
 *  the sweep is fresh without dumping a full timestamp into the header pill. */
function formatRelative(isoTs: string): string {
  const now = Date.now()
  const t = new Date(isoTs).getTime()
  const diff = Math.max(0, now - t)
  const min = Math.round(diff / 60_000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr}h ago`
  return new Date(isoTs).toLocaleDateString()
}
