// Missed-schedules panel — the detection policy ([meta.missed] in jobs.toml) + the last check's
// result on the Jobs list page, with an inline editor and a "Check now" button. Same shape and
// save flow as RetentionPanel: GET/PUT /admin/config/jobs/parsed + reload. Collapsed by default.
import { useCallback, useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { AlarmClockOff, ChevronDown, ChevronRight, Save, Search } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Input, Mono, SpinnerRing } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { relative } from './util'

interface MissedPolicy {
  enabled: boolean
  grace_minutes: number
  check_interval_minutes: number
  lookback_hours: number
}

interface MissedFire {
  job_id: string
  preset: string | null
  expected_at: string
  run_id: string | null
}

interface MissedReport {
  checked_at: string
  triggers: number
  missed: MissedFire[]
}

interface MissedStatus {
  policy: MissedPolicy
  last_check: MissedReport | null
}

interface JobsParsed {
  meta?: { version?: number; missed?: Partial<MissedPolicy> }
  jobs: unknown[]
}

const Panel = styled(Card)`display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; padding: 10px 14px;`
const Header = styled.button`
  font-family: inherit; background: transparent; border: 0; padding: 0;
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  color: ${colors.text.primary}; font-size: ${fontSize.sm};
  &:hover { color: ${colors.blue.main}; }
`
const HeaderLabel = styled.span`font-weight: 600;`
const HeaderSummary = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};`
const HeaderSpacer = styled.span`flex: 1;`
const Pill = styled.span<{ $tone: 'neutral' | 'red' }>`
  font-family: ${fonts.mono}; font-size: ${fontSize.micro};
  padding: 1px 6px; border-radius: ${radius.sm};
  background: ${({ $tone }) => $tone === 'red' ? colors.red.bg : 'transparent'};
  color: ${({ $tone }) => $tone === 'red' ? colors.red.main : colors.text.muted};
  border: 1px solid ${({ $tone }) => $tone === 'red' ? colors.red.border : colors.border};
`
const Body = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; align-items: end; margin-top: 4px;`
const Field = styled.label`display: flex; flex-direction: column; gap: 4px; font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const FieldLabel = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em;`
const Toggle = styled.button<{ $on: boolean }>`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  height: 32px; padding: 0 12px; border-radius: ${radius.sm}; cursor: pointer;
  border: 1px solid ${({ $on }) => $on ? colors.green.border : colors.border};
  background: ${({ $on }) => $on ? colors.green.bg : 'transparent'};
  color: ${({ $on }) => $on ? colors.green.main : colors.text.muted};
`
const Actions = styled.div`display: flex; gap: 8px; margin-top: 10px; align-items: center; flex-wrap: wrap;`
const MissedList = styled.ul`margin: 6px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 2px; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-family: ${fonts.mono};`

export interface MissedPanelProps {
  /** Called after a check so the Jobs list refreshes (a MISSED row becomes a job's last run). */
  onChanged?: () => void
}

export function MissedPanel({ onChanged }: MissedPanelProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<MissedStatus | null>(null)
  const [draft, setDraft] = useState<MissedPolicy | null>(null)
  const [busy, setBusy] = useState<'load' | 'save' | 'check' | null>('load')
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const load = useCallback(async () => {
    setBusy('load')
    setError(null)
    try {
      const r = await api.get<MissedStatus>('/admin/jobs/missed')
      setStatus(r)
      setDraft(r.policy)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const onSave = useCallback(async () => {
    if (!draft) return
    setBusy('save')
    setError(null)
    try {
      const parsed = await api.get<JobsParsed>('/admin/config/jobs/parsed')
      await api.put('/admin/config/jobs/parsed', { ...parsed, meta: { ...(parsed.meta ?? {}), missed: draft } })
      await api.post('/admin/reload')
      await load()
      setToast(t('nomaflow.missed.saved'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }, [draft, load, t])

  const onCheck = useCallback(async () => {
    setBusy('check')
    setError(null)
    try {
      const r = await api.post<MissedReport>('/admin/jobs/missed/check')
      setStatus((s) => (s ? { ...s, last_check: r } : s))
      setToast(t('nomaflow.missed.checked', { count: r.missed.length, triggers: r.triggers }))
      if (r.missed.length > 0) onChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }, [onChanged, t])

  const headerSummary = status
    ? status.policy.enabled
      ? t('nomaflow.missed.summary', { grace: status.policy.grace_minutes, interval: status.policy.check_interval_minutes })
      : t('nomaflow.missed.disabled')
    : '…'
  const last = status?.last_check ?? null
  const lastPill = last ? (
    <Pill $tone={last.missed.length > 0 ? 'red' : 'neutral'}>
      {t('nomaflow.missed.lastChecked', { when: relative(last.checked_at), count: last.missed.length })}
    </Pill>
  ) : null

  return (
    <Panel>
      <Header type="button" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <AlarmClockOff size={14} />
        <HeaderLabel>{t('nomaflow.missed.title')}</HeaderLabel>
        <HeaderSummary>· {headerSummary}</HeaderSummary>
        <HeaderSpacer />
        {lastPill}
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
                  <FieldLabel>{t('nomaflow.missed.enabledLabel')}</FieldLabel>
                  <Toggle type="button" $on={draft.enabled} onClick={() => setDraft({ ...draft, enabled: !draft.enabled })}>
                    {draft.enabled ? t('nomaflow.retention.on') : t('nomaflow.retention.off')}
                  </Toggle>
                </Field>
                <Field>
                  <FieldLabel>{t('nomaflow.missed.graceLabel')}</FieldLabel>
                  <Input type="number" min={1} max={1440} value={draft.grace_minutes}
                    onChange={(e) => setDraft({ ...draft, grace_minutes: clamp(parseInt(e.target.value || '0', 10), 1, 1440) })} />
                </Field>
                <Field>
                  <FieldLabel>{t('nomaflow.missed.intervalLabel')}</FieldLabel>
                  <Input type="number" min={1} max={1440} value={draft.check_interval_minutes}
                    onChange={(e) => setDraft({ ...draft, check_interval_minutes: clamp(parseInt(e.target.value || '0', 10), 1, 1440) })} />
                </Field>
                <Field>
                  <FieldLabel>{t('nomaflow.missed.lookbackLabel')}</FieldLabel>
                  <Input type="number" min={1} max={168} value={draft.lookback_hours}
                    onChange={(e) => setDraft({ ...draft, lookback_hours: clamp(parseInt(e.target.value || '0', 10), 1, 168) })} />
                </Field>
              </Body>
              <Actions>
                <Button $variant="primary" $size="sm" onClick={onSave} disabled={busy !== null || !policyChanged(draft, status?.policy)}>
                  {busy === 'save' ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />}
                  {t('common.save')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={onCheck} disabled={busy !== null}>
                  {busy === 'check' ? <SpinnerRing size={13} thickness={2} /> : <Search size={13} />}
                  {t('nomaflow.missed.checkNow')}
                </Button>
                {toast && <Mono style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{toast}</Mono>}
              </Actions>
              {last && last.missed.length > 0 && (
                <MissedList>
                  {last.missed.map((m) => (
                    <li key={`${m.job_id}:${m.expected_at}`}>
                      {m.job_id}{m.preset ? ` · ${m.preset}` : ''} — {new Date(m.expected_at).toLocaleString()}
                    </li>
                  ))}
                </MissedList>
              )}
            </>
          )}
        </>
      )}
    </Panel>
  )
}

function clamp(v: number, min: number, max: number): number {
  if (!Number.isFinite(v)) return min
  return Math.min(Math.max(v, min), max)
}

function policyChanged(draft: MissedPolicy, current: MissedPolicy | undefined): boolean {
  if (!current) return false
  return draft.enabled !== current.enabled
    || draft.grace_minutes !== current.grace_minutes
    || draft.check_interval_minutes !== current.check_interval_minutes
    || draft.lookback_hours !== current.lookback_hours
}
