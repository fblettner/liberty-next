// nomaflow schedule landscape — "what runs when". Orchestration is within-job (no DAG), so this
// is purely a scheduling view built from GET /admin/jobs: the upcoming fires grouped by day (a
// job's own cron and each schedulable preset are separate entries), the schedules that exist but
// are switched off, and — folded away — the jobs that only run by hand.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { CalendarClock, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Tag, Mono } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { describeCron } from '../../services/cronText'
import type { JobSummary, JobsListResponse } from './types'
import { STATE_TONE, relative } from './util'

const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1;`
const Stat = styled.span`
  display: inline-flex; align-items: baseline; gap: 6px; padding: 5px 12px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & b { font-size: ${fontSize.md}; color: ${colors.text.primary}; }
`
const Section = styled.section`margin-bottom: 18px;`
const SectionHead = styled.button`
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 0; margin: 0 0 8px; border: none; background: none; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary}; text-transform: uppercase; letter-spacing: 0.04em;
  &:disabled { cursor: default; }
`
const Group = styled.div`flex: none; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; overflow: hidden; margin-bottom: 10px;`
const DayHead = styled.div`padding: 7px 12px; background: ${colors.bg.input}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary};`
const Row = styled.div`
  display: grid; grid-template-columns: 80px minmax(200px, 1.3fr) minmax(0, 1.5fr) 110px 110px; align-items: center; gap: 12px;
  padding: 8px 12px; border-top: 1px solid ${colors.border}; cursor: pointer; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:hover { background: var(--hover-subtle); }
  & .time { font-family: ${fonts.mono}; font-size: ${fontSize.md}; color: ${colors.text.primary}; white-space: nowrap; }
  & .id { font-family: ${fonts.mono}; color: ${colors.text.primary}; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .sched { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
  & .sched .cron { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .rel { color: ${colors.text.muted}; }
  @media (max-width: 900px) { grid-template-columns: 56px minmax(0, 1fr) 100px; & .sched, & .rel { display: none; } }
`
const PresetTag = styled.span`
  margin-left: 8px; padding: 1px 7px; border-radius: 999px; font-family: ${fonts.sans}; font-weight: 400; font-size: ${fontSize.micro};
  background: ${colors.blue.bg}; border: 1px solid ${colors.blue.border}; color: ${colors.text.secondary};
`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 6px;`
const Chip = styled.button`
  padding: 3px 10px; border-radius: 999px; border: 1px solid ${colors.border}; background: ${colors.bg.card}; cursor: pointer;
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.border}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 10px 2px;`

/** One scheduled thing = a job's own cron OR one of its schedulable presets. */
type SchedEntry = {
  key: string
  jobId: string
  preset: string | null      // null = the job's own schedule; else the preset name
  schedule: string | null    // cron; null = manual-only
  enabled: boolean           // whether this trigger fires
  nextRun: string | null
  job: JobSummary
}

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`

export default function Schedule() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<JobSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showManual, setShowManual] = useState(false)

  const load = useCallback(() => {
    setError(null)
    api.get<JobsListResponse>('/admin/jobs')
      .then((r) => setJobs(r.jobs))
      .catch((e) => setError(e instanceof ApiError
        ? (e.status === 403 ? t('nomaflow.superuserRequired') : e.message)
        : String(e)))
  }, [t])
  useEffect(load, [load])

  const { upcoming, off, manual } = useMemo(() => {
    const entries: SchedEntry[] = []
    const manualIds: string[] = []
    for (const j of jobs ?? []) {
      if (j.schedule) entries.push({ key: j.id, jobId: j.id, preset: null, schedule: j.schedule, enabled: j.enabled, nextRun: j.schedule_next_run ?? null, job: j })
      for (const ps of j.preset_schedules ?? []) {
        entries.push({ key: `${j.id}::${ps.name}`, jobId: j.id, preset: ps.name, schedule: ps.schedule, enabled: ps.enabled, nextRun: ps.next_run, job: j })
      }
      if (!j.schedule && (j.preset_schedules?.length ?? 0) === 0) manualIds.push(j.id)
    }
    return {
      upcoming: entries.filter((e) => e.nextRun != null).sort((a, b) => (a.nextRun! < b.nextRun! ? -1 : 1)),
      off: entries.filter((e) => e.nextRun == null).sort((a, b) => a.key.localeCompare(b.key)),
      manual: manualIds.sort(),
    }
  }, [jobs])

  // the upcoming fires, one group per calendar day (Today / Tomorrow / the date)
  const days = useMemo(() => {
    const today = new Date()
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1)
    const groups: { label: string; entries: SchedEntry[] }[] = []
    let current = ''
    for (const e of upcoming) {
      const d = new Date(e.nextRun!)
      const k = dayKey(d)
      if (k !== current) {
        current = k
        const label = k === dayKey(today) ? t('nomaflow.scheduleView.today')
          : k === dayKey(tomorrow) ? t('nomaflow.scheduleView.tomorrow')
          : d.toLocaleDateString(i18n.language, { weekday: 'long', day: 'numeric', month: 'long' })
        groups.push({ label, entries: [] })
      }
      groups[groups.length - 1].entries.push(e)
    }
    return groups
  }, [upcoming, t, i18n.language])
  const next24h = upcoming.filter((e) => new Date(e.nextRun!).getTime() - Date.now() < 86_400_000).length

  const open = (jobId: string) => navigate(`/nomaflow/jobs/${encodeURIComponent(jobId)}`)
  const human = (cron: string | null) => {
    const d = describeCron(cron, i18n.language)
    return d ? t(d.key, d.values) : null
  }
  const lastRun = (j: JobSummary) => j.in_flight
    ? <Tag $tone="blue">{t('nomaflow.jobs.running')}</Tag>
    : j.last_run ? <Tag $tone={STATE_TONE[j.last_run.state]}>{j.last_run.state}</Tag> : <span>—</span>
  const row = (e: SchedEntry, withTime: boolean) => {
    const h = human(e.schedule)
    return (
      <Row key={e.key} onClick={() => open(e.jobId)} title={e.nextRun ? new Date(e.nextRun).toLocaleString() : undefined}>
        <span className="time">{withTime && e.nextRun ? new Date(e.nextRun).toLocaleTimeString(i18n.language, { hour: '2-digit', minute: '2-digit' }) : '—'}</span>
        <span className="id">{e.jobId}{e.preset && <PresetTag>{e.preset}</PresetTag>}</span>
        <span className="sched">
          <span>{h ?? <Mono>{e.schedule}</Mono>}</span>
          {h && <span className="cron">{e.schedule}</span>}
        </span>
        <span className="rel">{withTime && e.nextRun ? relative(e.nextRun) : <Tag $tone="neutral">{t('nomaflow.jobs.disabled')}</Tag>}</span>
        <span>{lastRun(e.job)}</span>
      </Row>
    )
  }

  return (
    <PageLayout icon={<CalendarClock size={18} />} title={t('nomaflow.scheduleView.title')} description={t('nomaflow.scheduleView.subtitle')}>
      <Toolbar>
        {jobs && (
          <>
            <Stat><b>{next24h}</b>{t('nomaflow.scheduleView.next24h')}</Stat>
            <Stat><b>{upcoming.length}</b>{t('nomaflow.scheduleView.active')}</Stat>
            <Stat><b>{off.length}</b>{t('nomaflow.scheduleView.off')}</Stat>
            <Stat><b>{manual.length}</b>{t('nomaflow.scheduleView.manualOnly')}</Stat>
          </>
        )}
        <Spacer />
        <Button $variant="ghost" $size="sm" onClick={load}><RefreshCw size={14} /> {t('common.reload')}</Button>
      </Toolbar>

      {error && <Banner $tone="error">{error}</Banner>}
      {jobs == null && !error && <Centered />}

      {jobs && (
        <>
          <Section>
            <SectionHead disabled>{t('nomaflow.scheduleView.upcoming', { count: upcoming.length })}</SectionHead>
            {days.length === 0 ? <Empty>{t('nomaflow.scheduleView.noScheduled')}</Empty> : days.map((g) => (
              <Group key={g.label}>
                <DayHead>{g.label}</DayHead>
                {g.entries.map((e) => row(e, true))}
              </Group>
            ))}
          </Section>

          {off.length > 0 && (
            <Section>
              <SectionHead disabled>{t('nomaflow.scheduleView.offTitle', { count: off.length })}</SectionHead>
              <Group>{off.map((e) => row(e, false))}</Group>
            </Section>
          )}

          {manual.length > 0 && (
            <Section>
              <SectionHead type="button" onClick={() => setShowManual((v) => !v)}>
                {showManual ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                {t('nomaflow.scheduleView.manual', { count: manual.length })}
              </SectionHead>
              {showManual && <Chips>{manual.map((id) => <Chip key={id} type="button" onClick={() => open(id)}>{id}</Chip>)}</Chips>}
            </Section>
          )}
        </>
      )}
    </PageLayout>
  )
}
