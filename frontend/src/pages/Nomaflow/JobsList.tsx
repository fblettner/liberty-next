// nomaflow Jobs list — the feature-area home (NOMAFLOW-UI.md §3.1). Lists the job
// catalogue with last-run badges + next-fire, and carries the per-job operational
// actions: Run now, Cancel, enable toggle, Edit. New Job + Reload in the header.
//
// Backed by GET /admin/jobs (catalogue + last_run + next_run). The enable toggle is
// a real save — it round-trips through GET/PUT /admin/config/jobs/parsed + reload
// (NOMAFLOW-UI.md §3.1) — so it shows a spinner, not an optimistic flip.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Play, Ban, Pencil, Plus, RefreshCw, Workflow, Clock, CalendarClock } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Card, Tag, Mono, SpinnerRing } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { JobSummary, JobsListResponse, JobsParsedResponse } from './types'
import { STATE_TONE, relative } from './util'

const Toolbar = styled.div`
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px;
`
const ToolbarSpacer = styled.div`flex: 1;`
const List = styled.div`display: flex; flex-direction: column; gap: 10px;`
const JobCard = styled(Card)`display: flex; flex-direction: column; gap: 8px;`
const CardTop = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap;`
const JobId = styled.span`font-family: ${fonts.mono}; font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.md};`
const Desc = styled.div`color: ${colors.text.secondary}; font-size: ${fontSize.sm};`
const MetaRow = styled.div`
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
`
const Meta = styled.span`display: inline-flex; align-items: center; gap: 5px;`
const Actions = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 28px 4px; text-align: center;`
// The last-run state badge, clickable → that run's detail page (its log).
const RunLink = styled.span`display: inline-flex; cursor: pointer; &:hover { opacity: 0.8; }`
// A real toggle styled as a pill — clearly clickable, clearly stateful.
const Toggle = styled.button<{ $on: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px;
  border-radius: ${radius.sm}; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  border: 1px solid ${({ $on }) => ($on ? colors.green.border : colors.border)};
  background: ${({ $on }) => ($on ? colors.green.bg : 'transparent')};
  color: ${({ $on }) => ($on ? colors.green.main : colors.text.muted)};
  &:disabled { opacity: 0.5; cursor: default; }
`

export default function JobsList() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<JobSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)   // job id with an action in flight

  const load = useCallback(() => {
    setError(null)
    api.get<JobsListResponse>('/admin/jobs')
      .then((r) => setJobs(r.jobs))
      .catch((e) => setError(e instanceof ApiError
        ? (e.status === 403 ? t('nomaflow.superuserRequired') : e.message)
        : String(e)))
  }, [t])
  useEffect(load, [load])

  const runNow = useCallback(async (job: JobSummary) => {
    setBusyId(job.id); setError(null)
    try {
      // Synchronous endpoint — resolves when the run reaches a terminal state.
      await api.post(`/admin/jobs/${encodeURIComponent(job.id)}/run`)
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [load])

  const cancelRun = useCallback(async (job: JobSummary) => {
    if (!job.last_run) return
    setBusyId(job.id); setError(null)
    try {
      await api.post(`/admin/jobs/runs/${encodeURIComponent(job.last_run.run_id)}/cancel`)
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [load])

  // Enable/disable is a config edit: read jobs.toml, flip the one job's `enabled`,
  // write it back, reload. Not a cheap flag — hence the spinner + the confirm-free
  // but clearly-busy UX.
  const toggleEnabled = useCallback(async (job: JobSummary) => {
    setBusyId(job.id); setError(null)
    try {
      const parsed = await api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
      const next = parsed.jobs.map((j) =>
        j.id === job.id ? { ...j, enabled: !job.enabled } : j)
      await api.put('/admin/config/jobs/parsed', { jobs: next })
      await api.post('/admin/reload')
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [load])

  const newJob = useCallback(async () => {
    navigate('/nomaflow/jobs/new')
  }, [navigate])

  const sorted = useMemo(
    () => (jobs ? [...jobs].sort((a, b) => a.id.localeCompare(b.id)) : null),
    [jobs],
  )

  const header = (
    <PageLayout
      icon={<Workflow size={18} />}
      title={t('nomaflow.jobs.title')}
      description={t('nomaflow.jobs.subtitle')}
    >
      <Toolbar>
        <Button $variant="primary" $size="sm" onClick={newJob}>
          <Plus size={14} /> {t('nomaflow.jobs.new')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={() => navigate('/nomaflow/schedule')}>
          <CalendarClock size={14} /> {t('nomaflow.jobs.scheduleView')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={load}>
          <RefreshCw size={14} /> {t('common.reload')}
        </Button>
        <ToolbarSpacer />
      </Toolbar>
      {error && <Banner $tone="error" style={{ marginBottom: 12 }}>{error}</Banner>}
      {sorted == null && !error ? <Centered /> : null}
      {sorted && sorted.length === 0 && (
        <Empty>{t('nomaflow.jobs.empty')}</Empty>
      )}
      {sorted && sorted.length > 0 && (
        <List>
          {sorted.map((job) => {
            const busy = busyId === job.id
            return (
              <JobCard key={job.id}>
                <CardTop>
                  <JobId>{job.id}</JobId>
                  {job.last_run && (
                    <RunLink
                      onClick={() => navigate(`/nomaflow/runs/${encodeURIComponent(job.last_run!.run_id)}`)}
                      title={t('nomaflow.jobs.viewRun')}
                    >
                      <Tag $tone={STATE_TONE[job.last_run.state]}>{job.last_run.state}</Tag>
                    </RunLink>
                  )}
                  {job.tags.map((tg) => <Tag key={tg}>{tg}</Tag>)}
                  <ToolbarSpacer />
                  <Toggle
                    $on={job.enabled}
                    disabled={busy}
                    onClick={() => toggleEnabled(job)}
                    title={t(job.enabled ? 'nomaflow.jobs.disable' : 'nomaflow.jobs.enable')}
                  >
                    {busy ? <SpinnerRing size={12} thickness={2} /> : null}
                    {job.enabled ? t('nomaflow.jobs.enabled') : t('nomaflow.jobs.disabled')}
                  </Toggle>
                </CardTop>
                {job.description && <Desc>{job.description}</Desc>}
                <MetaRow>
                  <Meta>
                    <Clock size={13} />
                    {job.schedule
                      ? <Mono>{job.schedule}</Mono>
                      : t('nomaflow.jobs.manualOnly')}
                  </Meta>
                  {job.next_run && (
                    <Meta title={new Date(job.next_run).toLocaleString()}>
                      <CalendarClock size={13} /> {t('nomaflow.jobs.nextRun')} {relative(job.next_run)}
                    </Meta>
                  )}
                  {job.last_run?.finished_at && (
                    <Meta title={new Date(job.last_run.finished_at).toLocaleString()}>
                      {t('nomaflow.jobs.lastRun')} {relative(job.last_run.finished_at)}
                    </Meta>
                  )}
                  <Meta>{t('nomaflow.jobs.stepCount', { count: job.step_count })}</Meta>
                </MetaRow>
                <Actions>
                  <Button
                    $variant="ghost" $size="sm" disabled={busy || job.in_flight}
                    onClick={() => runNow(job)}
                  >
                    {busy ? <SpinnerRing size={13} thickness={2} /> : <Play size={13} />}
                    {t('nomaflow.jobs.runNow')}
                  </Button>
                  {job.in_flight && (
                    <Button $variant="danger" $size="sm" disabled={busy} onClick={() => cancelRun(job)}>
                      <Ban size={13} /> {t('nomaflow.jobs.cancel')}
                    </Button>
                  )}
                  <Button
                    $variant="ghost" $size="sm" disabled={busy}
                    onClick={() => navigate(`/nomaflow/jobs/${encodeURIComponent(job.id)}`)}
                  >
                    <Pencil size={13} /> {t('common.edit')}
                  </Button>
                </Actions>
              </JobCard>
            )
          })}
        </List>
      )}
    </PageLayout>
  )
  return header
}
