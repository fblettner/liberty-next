// nomaflow schedule landscape (NOMAFLOW-UI.md §3.4) — the cross-job "what runs when"
// overview. Orchestration is within-job (no DAG), so this is purely a scheduling view:
// every scheduled job sorted by its next fire, with the manual-only / disabled jobs
// listed apart. Built entirely from GET /admin/jobs — no extra backend.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { CalendarClock, ArrowLeft, RefreshCw } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Tag, Mono } from '../../common'
import { colors, fontSize, fonts } from '../../theme'
import type { JobSummary, JobsListResponse } from './types'
import { STATE_TONE, relative } from './util'

const Toolbar = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 14px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1;`
const GroupHead = styled.div`
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};
  text-transform: uppercase; letter-spacing: 0.04em; margin: 14px 0 6px;
`
const Table = styled.div`display: flex; flex-direction: column;`
const HeaderRow = styled.div`
  display: grid; grid-template-columns: 1.6fr 1.2fr 1.4fr 1fr;
  gap: 12px; padding: 6px 10px; font-size: ${fontSize.sm}; color: ${colors.text.muted};
`
const Row = styled.div`
  display: grid; grid-template-columns: 1.6fr 1.2fr 1.4fr 1fr;
  gap: 12px; padding: 9px 10px; align-items: center; cursor: pointer;
  border-top: 1px solid ${colors.border};
  &:hover { background: var(--hover-subtle, ${colors.bg.card}); }
`
const JobId = styled.span`font-family: ${fonts.mono}; color: ${colors.text.primary}; display: inline-flex; align-items: center;`
const Cell = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary}; display: inline-flex; align-items: center; gap: 6px;`
const PresetTag = styled.span`
  margin-left: 8px; padding: 1px 7px; border-radius: 999px;
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  background: ${colors.bg.card}; border: 1px solid ${colors.border}; color: ${colors.text.secondary};
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 16px 10px;`

/** One scheduled thing = a job's own cron OR one of its schedulable presets. */
type SchedEntry = {
  key: string
  jobId: string
  label: string
  preset: string | null      // null = the job's own schedule; else the preset name
  schedule: string | null    // cron; null = manual-only
  nextRun: string | null
  job: JobSummary
}

export default function Schedule() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<JobSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    api.get<JobsListResponse>('/admin/jobs')
      .then((r) => setJobs(r.jobs))
      .catch((e) => setError(e instanceof ApiError
        ? (e.status === 403 ? t('nomaflow.superuserRequired') : e.message)
        : String(e)))
  }, [t])
  useEffect(load, [load])

  // One entry PER SCHEDULE, not per job: a job's own cron (if any) plus each
  // schedulable preset's cron. Scheduled = has a next fire; the rest (manual-only,
  // disabled — incl. a preset whose job is disabled so it never fires) land below.
  const { scheduled, unscheduled } = useMemo(() => {
    const all = jobs ?? []
    const entries: SchedEntry[] = []
    for (const j of all) {
      if (j.schedule) {
        entries.push({ key: j.id, jobId: j.id, label: j.id, preset: null, schedule: j.schedule, nextRun: j.schedule_next_run ?? null, job: j })
      }
      for (const ps of j.preset_schedules ?? []) {
        entries.push({ key: `${j.id}::${ps.name}`, jobId: j.id, label: j.id, preset: ps.name, schedule: ps.schedule, nextRun: ps.next_run, job: j })
      }
      // A job with neither a job-level cron nor any scheduled preset is manual-only.
      if (!j.schedule && (j.preset_schedules?.length ?? 0) === 0) {
        entries.push({ key: j.id, jobId: j.id, label: j.id, preset: null, schedule: null, nextRun: null, job: j })
      }
    }
    const sch = entries.filter((e) => e.nextRun != null).sort((a, b) => (a.nextRun! < b.nextRun! ? -1 : 1))
    const un = entries.filter((e) => e.nextRun == null).sort((a, b) => a.key.localeCompare(b.key))
    return { scheduled: sch, unscheduled: un }
  }, [jobs])

  const row = (e: SchedEntry) => (
    <Row key={e.key} onClick={() => navigate(`/nomaflow/jobs/${encodeURIComponent(e.jobId)}`)}>
      <JobId>
        {e.label}
        {e.preset && <PresetTag>{e.preset}</PresetTag>}
      </JobId>
      <Cell>{e.schedule ? <Mono>{e.schedule}</Mono> : t('nomaflow.jobs.manualOnly')}</Cell>
      <Cell>
        {e.nextRun
          ? <span title={new Date(e.nextRun).toLocaleString()}>
              <CalendarClock size={13} /> {new Date(e.nextRun).toLocaleString()} · {relative(e.nextRun)}
            </span>
          : '—'}
      </Cell>
      <Cell>
        {e.job.in_flight && <Tag $tone="blue">{t('nomaflow.jobs.running')}</Tag>}
        {!e.job.in_flight && e.job.last_run && <Tag $tone={STATE_TONE[e.job.last_run.state]}>{e.job.last_run.state}</Tag>}
        {!e.job.in_flight && !e.job.last_run && '—'}
      </Cell>
    </Row>
  )

  return (
    <PageLayout
      icon={<CalendarClock size={18} />}
      title={t('nomaflow.scheduleView.title')}
      description={t('nomaflow.scheduleView.subtitle')}
    >
      <Toolbar>
        <Button $variant="ghost" $size="sm" onClick={() => navigate('/nomaflow')}>
          <ArrowLeft size={14} /> {t('nomaflow.editor.backToJobs')}
        </Button>
        <Spacer />
        <Button $variant="ghost" $size="sm" onClick={load}>
          <RefreshCw size={14} /> {t('common.reload')}
        </Button>
      </Toolbar>

      {error && <Banner $tone="error">{error}</Banner>}
      {jobs == null && !error && <Centered />}

      {jobs && (
        <>
          <GroupHead>{t('nomaflow.scheduleView.upcoming', { count: scheduled.length })}</GroupHead>
          {scheduled.length === 0 ? (
            <Empty>{t('nomaflow.scheduleView.noScheduled')}</Empty>
          ) : (
            <Table>
              <HeaderRow>
                <span>{t('nomaflow.scheduleView.colJob')}</span>
                <span>{t('nomaflow.scheduleView.colSchedule')}</span>
                <span>{t('nomaflow.scheduleView.colNextRun')}</span>
                <span>{t('nomaflow.scheduleView.colLastRun')}</span>
              </HeaderRow>
              {scheduled.map(row)}
            </Table>
          )}

          {unscheduled.length > 0 && (
            <>
              <GroupHead>{t('nomaflow.scheduleView.manual', { count: unscheduled.length })}</GroupHead>
              <Table>
                <HeaderRow>
                  <span>{t('nomaflow.scheduleView.colJob')}</span>
                  <span>{t('nomaflow.scheduleView.colSchedule')}</span>
                  <span>{t('nomaflow.scheduleView.colNextRun')}</span>
                  <span>{t('nomaflow.scheduleView.colLastRun')}</span>
                </HeaderRow>
                {unscheduled.map(row)}
              </Table>
            </>
          )}
        </>
      )}
    </PageLayout>
  )
}
