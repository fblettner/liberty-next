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
const JobId = styled.span`font-family: ${fonts.mono}; color: ${colors.text.primary};`
const Cell = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary}; display: inline-flex; align-items: center; gap: 6px;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 16px 10px;`

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

  // Scheduled = has a next fire; sort ascending by it. The rest (manual-only,
  // disabled) land in a separate group below — they have no place on a timeline.
  const { scheduled, unscheduled } = useMemo(() => {
    const all = jobs ?? []
    const sch = all.filter((j) => j.next_run != null)
      .sort((a, b) => (a.next_run! < b.next_run! ? -1 : 1))
    const un = all.filter((j) => j.next_run == null)
      .sort((a, b) => a.id.localeCompare(b.id))
    return { scheduled: sch, unscheduled: un }
  }, [jobs])

  const row = (job: JobSummary) => (
    <Row key={job.id} onClick={() => navigate(`/nomaflow/jobs/${encodeURIComponent(job.id)}`)}>
      <JobId>{job.id}</JobId>
      <Cell>{job.schedule ? <Mono>{job.schedule}</Mono> : t('nomaflow.jobs.manualOnly')}</Cell>
      <Cell>
        {job.next_run
          ? <span title={new Date(job.next_run).toLocaleString()}>
              <CalendarClock size={13} /> {new Date(job.next_run).toLocaleString()} · {relative(job.next_run)}
            </span>
          : '—'}
      </Cell>
      <Cell>
        {job.in_flight && <Tag $tone="blue">{t('nomaflow.jobs.running')}</Tag>}
        {!job.in_flight && job.last_run && <Tag $tone={STATE_TONE[job.last_run.state]}>{job.last_run.state}</Tag>}
        {!job.in_flight && !job.last_run && '—'}
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
