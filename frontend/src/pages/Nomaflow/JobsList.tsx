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
import { PageLayout, Button, Banner, Centered, Card, Tag, Mono, SpinnerRing, Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Checkbox, Input } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { JobSummary, JobsListResponse, JobsParsedResponse, StepConfig } from './types'
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
// Run-with-parameters modal — one section per python step, each kwarg as a typed input.
const ParamSection = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 12px;
  display: flex; flex-direction: column; gap: 8px;
`
const ParamSectionTitle = styled.div`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  display: flex; align-items: baseline; gap: 8px;
`
const ParamSectionType = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const ParamRow = styled.label`
  display: grid; grid-template-columns: 200px 1fr; align-items: center; gap: 10px;
  font-size: ${fontSize.sm};
`
const ParamKey = styled.span`font-family: ${fonts.mono}; color: ${colors.text.secondary};`
const ParamHint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; margin-top: -4px;`

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

  // Auto-refresh while any job is in flight. POST /admin/jobs/{id}/run is
  // fire-and-return now (PHASE13/runner: create_run + execute_run), so without
  // polling the list would freeze on the QUEUED → RUNNING → terminal transitions
  // — which is exactly what the operator wants to watch. RunDetail polls on the
  // same 2s cadence; we stop when nothing is in flight to keep the page quiet.
  const POLL_MS = 2000
  const anyInFlight = useMemo(
    () => jobs ? jobs.some((j) => j.in_flight) : false,
    [jobs],
  )
  useEffect(() => {
    if (!anyInFlight) return
    const id = window.setInterval(load, POLL_MS)
    return () => window.clearInterval(id)
  }, [anyInFlight, load])

  // "Run with parameters" modal state. When a job has python steps carrying op_kwargs the
  // operator gets a chance to override values per-fire (target_connector, apps_id, …) before
  // the run is queued. Jobs without overridable kwargs skip the modal entirely.
  const [paramModalJob, setParamModalJob] = useState<{ job: JobSummary; steps: StepConfig[] } | null>(null)

  const _postRun = useCallback(async (jobId: string, overrides?: Record<string, Record<string, unknown>>) => {
    setBusyId(jobId); setError(null)
    try {
      // Fire-and-return: the endpoint creates the run row + spawns execution
      // as a background task, returns immediately. Refresh once to pick up
      // the new RUNNING badge — the in_flight poll then takes over from there.
      const body = overrides && Object.keys(overrides).length > 0 ? { op_kwargs: overrides } : undefined
      await api.post(`/admin/jobs/${encodeURIComponent(jobId)}/run`, body)
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [load])

  const runNow = useCallback(async (job: JobSummary) => {
    // If the job has any python step with non-empty op_kwargs, open the modal so the
    // operator can edit values per fire. Otherwise fire immediately — the modal would
    // be an empty form and a useless extra click.
    setError(null); setBusyId(job.id)
    try {
      const parsed = await api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
      const def = parsed.jobs.find((j) => j.id === job.id)
      const pythonSteps = (def?.steps ?? []).filter((s) =>
        s.type === 'python' && s.op_kwargs && typeof s.op_kwargs === 'object' && Object.keys(s.op_kwargs as object).length > 0,
      )
      if (pythonSteps.length === 0) {
        setBusyId(null)
        await _postRun(job.id)
        return
      }
      setBusyId(null)
      setParamModalJob({ job, steps: pythonSteps })
    } catch (e) {
      setBusyId(null)
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [_postRun])

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
  return (
    <>
      {header}
      {paramModalJob && (
        <RunWithParamsModal
          job={paramModalJob.job}
          pythonSteps={paramModalJob.steps}
          onCancel={() => setParamModalJob(null)}
          onSubmit={async (overrides) => {
            setParamModalJob(null)
            await _postRun(paramModalJob.job.id, overrides)
          }}
        />
      )}
    </>
  )
}


// "Run with parameters" — modal that lets the operator override op_kwargs for one fire of
// a job without editing jobs.toml. One section per python step; one typed input per kwarg.
// Submit POSTs as the `op_kwargs` body the runner merges into the saved values.
function RunWithParamsModal({
  job, pythonSteps, onCancel, onSubmit,
}: {
  job: JobSummary
  pythonSteps: StepConfig[]
  onCancel: () => void
  onSubmit: (overrides: Record<string, Record<string, unknown>>) => Promise<void> | void
}) {
  const { t } = useTranslation()
  // Working copy of each step's kwargs, keyed by step name. We seed from the saved values
  // and let the operator edit; on Submit we diff against the saved values and only send
  // the changed keys (smaller payload, clearer server log).
  const initial = useMemo(() => {
    const out: Record<string, Record<string, unknown>> = {}
    for (const s of pythonSteps) {
      out[s.name] = { ...((s.op_kwargs as Record<string, unknown>) ?? {}) }
    }
    return out
  }, [pythonSteps])
  const [values, setValues] = useState<Record<string, Record<string, unknown>>>(initial)
  const [busy, setBusy] = useState(false)

  const setKey = (stepName: string, key: string, v: unknown) => {
    setValues((prev) => ({ ...prev, [stepName]: { ...prev[stepName], [key]: v } }))
  }

  const submit = async () => {
    // Build overrides as the diff vs. initial — keep the payload minimal and the server
    // log easy to read (operator sees exactly what they changed).
    const overrides: Record<string, Record<string, unknown>> = {}
    for (const s of pythonSteps) {
      const before = initial[s.name] ?? {}
      const after = values[s.name] ?? {}
      const diff: Record<string, unknown> = {}
      for (const k of Object.keys(after)) {
        if (!Object.is(after[k], before[k])) diff[k] = after[k]
      }
      if (Object.keys(diff).length > 0) overrides[s.name] = diff
    }
    setBusy(true)
    try {
      await onSubmit(overrides)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Overlay onClick={onCancel}>
      <Modal style={{ width: 'min(640px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          {t('nomaflow.runParams.title', 'Run with parameters')} ·{' '}
          <Mono style={{ color: colors.text.muted, fontWeight: 400 }}>{job.id}</Mono>
        </ModalHeader>
        <ModalBody>
          <div style={{ color: colors.text.muted, fontSize: fontSize.sm }}>
            {t('nomaflow.runParams.hint',
              'Values apply to this fire only — jobs.toml stays unchanged. Leave a field as-is to use its saved value.')}
          </div>
          {pythonSteps.map((s) => (
            <ParamSection key={s.name}>
              <ParamSectionTitle>
                <span>{s.name}</span>
                <ParamSectionType>· {String((s as { callable?: unknown }).callable ?? '')}</ParamSectionType>
              </ParamSectionTitle>
              {Object.entries(values[s.name] ?? {}).map(([k, v]) => (
                <ParamRow key={k}>
                  <ParamKey>{k}</ParamKey>
                  {typeof v === 'boolean' ? (
                    <Checkbox checked={v} onChange={(checked) => setKey(s.name, k, checked)} />
                  ) : typeof v === 'number' ? (
                    <Input
                      type="number"
                      value={Number.isFinite(v as number) ? String(v) : ''}
                      onChange={(e) => {
                        const n = e.target.value === '' ? null : Number(e.target.value)
                        setKey(s.name, k, n)
                      }}
                    />
                  ) : (
                    <Input
                      value={v == null ? '' : String(v)}
                      onChange={(e) => setKey(s.name, k, e.target.value)}
                    />
                  )}
                </ParamRow>
              ))}
              {Object.keys(values[s.name] ?? {}).length === 0 && (
                <ParamHint>{t('nomaflow.runParams.noKwargs', '(no parameters on this step)')}</ParamHint>
              )}
            </ParamSection>
          ))}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" onClick={onCancel} disabled={busy}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button $variant="primary" onClick={submit} disabled={busy}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Play size={13} />}{' '}
            {t('nomaflow.runParams.run', 'Run')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
