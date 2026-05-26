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
import { PageLayout, Button, Banner, Centered, Card, Tag, Mono, SpinnerRing, Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Checkbox, Input, Select, SearchSelect, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
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

  // "Run with parameters" modal state. The modal carries both the job-level params
  // (shared across all steps) AND each python step's op_kwargs. Submit sends a
  // {params, op_kwargs} body the runner merges with the saved config.
  const [paramModalJob, setParamModalJob] = useState<
    { job: JobSummary; jobParams: Record<string, unknown>; steps: StepConfig[]; logLevel: 'INFO' | 'DEBUG' } | null
  >(null)

  const _postRun = useCallback(async (
    jobId: string,
    body?: {
      params?: Record<string, unknown>
      op_kwargs?: Record<string, Record<string, unknown>>
      log_level?: 'INFO' | 'DEBUG'
    },
  ) => {
    setBusyId(jobId); setError(null)
    try {
      // Fire-and-return: the endpoint creates the run row + spawns execution
      // as a background task, returns immediately. Refresh once to pick up
      // the new RUNNING badge — the in_flight poll then takes over from there.
      const has = body && (
        (body.params && Object.keys(body.params).length > 0) ||
        (body.op_kwargs && Object.keys(body.op_kwargs).length > 0) ||
        body.log_level !== undefined
      )
      await api.post(`/admin/jobs/${encodeURIComponent(jobId)}/run`, has ? body : undefined)
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [load])

  const runNow = useCallback(async (job: JobSummary) => {
    // Open the modal when the job carries job-level params OR any python step with
    // non-empty op_kwargs. Otherwise fire immediately — the modal would be empty.
    setError(null); setBusyId(job.id)
    try {
      const parsed = await api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
      const def = parsed.jobs.find((j) => j.id === job.id)
      const jobParams = (def?.params ?? {}) as Record<string, unknown>
      const pythonSteps = (def?.steps ?? []).filter((s) =>
        s.type === 'python' && s.op_kwargs && typeof s.op_kwargs === 'object' && Object.keys(s.op_kwargs as object).length > 0,
      )
      const jobLogLevel: 'INFO' | 'DEBUG' = (def?.log_level === 'DEBUG') ? 'DEBUG' : 'INFO'
      const hasAnything = Object.keys(jobParams).length > 0 || pythonSteps.length > 0
      if (!hasAnything) {
        // No params + no per-step kwargs — quick-fire at the job's saved
        // log_level. Operators who need to flip to DEBUG on a no-params job
        // can edit jobs.toml ``log_level = "DEBUG"`` (saved default) until we
        // add a separate "Run with options" entry for the no-params case.
        setBusyId(null)
        await _postRun(job.id)
        return
      }
      setBusyId(null)
      setParamModalJob({ job, jobParams, steps: pythonSteps, logLevel: jobLogLevel })
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
          jobParams={paramModalJob.jobParams}
          pythonSteps={paramModalJob.steps}
          jobLogLevel={paramModalJob.logLevel}
          onCancel={() => setParamModalJob(null)}
          onSubmit={async (body) => {
            setParamModalJob(null)
            await _postRun(paramModalJob.job.id, body)
          }}
        />
      )}
    </>
  )
}


// "Run with parameters" — modal that lets the operator override op_kwargs for one fire of
// a job without editing jobs.toml. Two sections:
//   * Shared params (the job-level kwargs merged under every step)
//   * One section per python step with its own op_kwargs
// On submit, sends only the diff vs. saved as a `{params, op_kwargs}` body — the runner
// merges with the saved config (layer order: job.params → step.op_kwargs → params override
// → per-step op_kwargs override, each layer winning over the previous).
//
// Inputs are type-aware: boolean → Checkbox; number → number input; string → text input;
// and a key ending in `_connector` (or named exactly `connector`) → SearchSelect of the
// available connectors loaded from the workspace. Saves typos vs free-text.
function RunWithParamsModal({
  job, jobParams, pythonSteps, jobLogLevel, onCancel, onSubmit,
}: {
  job: JobSummary
  jobParams: Record<string, unknown>
  pythonSteps: StepConfig[]
  /** The job's saved log_level from jobs.toml (defaults to 'INFO'). The dropdown
   *  is seeded from this; on submit we only send log_level when the operator
   *  picked a value DIFFERENT from this saved default. */
  jobLogLevel: 'INFO' | 'DEBUG'
  onCancel: () => void
  onSubmit: (body: {
    params?: Record<string, unknown>
    op_kwargs?: Record<string, Record<string, unknown>>
    log_level?: 'INFO' | 'DEBUG'
  }) => Promise<void> | void
}) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  const connectorOptions = useMemo<SearchSelectOption[]>(
    () => (connectors ?? []).map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [connectors],
  )

  // Working copies — seed from saved values, diff on submit so we only send what changed.
  const initialStepKwargs = useMemo(() => {
    const out: Record<string, Record<string, unknown>> = {}
    for (const s of pythonSteps) {
      out[s.name] = { ...((s.op_kwargs as Record<string, unknown>) ?? {}) }
    }
    return out
  }, [pythonSteps])
  const [stepValues, setStepValues] = useState<Record<string, Record<string, unknown>>>(initialStepKwargs)
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({ ...jobParams })
  const [logLevel, setLogLevel] = useState<'INFO' | 'DEBUG'>(jobLogLevel)
  const [busy, setBusy] = useState(false)

  const setStepKey = (stepName: string, key: string, v: unknown) => {
    setStepValues((prev) => ({ ...prev, [stepName]: { ...prev[stepName], [key]: v } }))
  }
  const setParamKey = (key: string, v: unknown) => {
    setParamValues((prev) => ({ ...prev, [key]: v }))
  }

  const submit = async () => {
    // Per-step diff: only the kwargs that changed end up in the payload.
    const stepOverrides: Record<string, Record<string, unknown>> = {}
    for (const s of pythonSteps) {
      const before = initialStepKwargs[s.name] ?? {}
      const after = stepValues[s.name] ?? {}
      const diff: Record<string, unknown> = {}
      for (const k of Object.keys(after)) {
        if (!Object.is(after[k], before[k])) diff[k] = after[k]
      }
      if (Object.keys(diff).length > 0) stepOverrides[s.name] = diff
    }
    // Job-level params diff.
    const paramsDiff: Record<string, unknown> = {}
    for (const k of Object.keys(paramValues)) {
      if (!Object.is(paramValues[k], jobParams[k])) paramsDiff[k] = paramValues[k]
    }
    const body: {
      params?: Record<string, unknown>
      op_kwargs?: Record<string, Record<string, unknown>>
      log_level?: 'INFO' | 'DEBUG'
    } = {}
    if (Object.keys(paramsDiff).length > 0) body.params = paramsDiff
    if (Object.keys(stepOverrides).length > 0) body.op_kwargs = stepOverrides
    // Only send log_level when it differs from the job's saved default — keeps
    // the payload minimal + lets the runner fall back to job.log_level when
    // nothing's specified (matches the operator's intent: "I left the dropdown
    // alone, so use what's in jobs.toml").
    if (logLevel !== jobLogLevel) body.log_level = logLevel
    setBusy(true)
    try {
      await onSubmit(body)
    } finally {
      setBusy(false)
    }
  }

  // Render one row per kwarg, typed input based on value + key. Extracted so both the
  // job-level params section and each step's section share the rendering logic.
  const renderKwargRow = (key: string, v: unknown, setter: (v: unknown) => void) => {
    // _connector / connector → connector picker (saves typos vs free text).
    const isConnectorKey = key === 'connector' || key.endsWith('_connector')
    return (
      <ParamRow key={key}>
        <ParamKey>{key}</ParamKey>
        {isConnectorKey ? (
          <SearchSelect
            value={v == null ? '' : String(v)}
            options={connectorOptions}
            onChange={setter}
            placeholder={t('nomaflow.runParams.pickConnector', 'Pick a connector…')}
          />
        ) : typeof v === 'boolean' ? (
          <Checkbox checked={v} onChange={(checked) => setter(checked)} />
        ) : typeof v === 'number' ? (
          <Input
            type="number"
            value={Number.isFinite(v as number) ? String(v) : ''}
            onChange={(e) => setter(e.target.value === '' ? null : Number(e.target.value))}
          />
        ) : (
          <Input
            value={v == null ? '' : String(v)}
            onChange={(e) => setter(e.target.value)}
          />
        )}
      </ParamRow>
    )
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
          {/* Log-level override — per-fire-only. Defaults to the job's saved
              log_level from jobs.toml; submit only includes the field when the
              operator changed it. DEBUG emits the full SQL of every run_query
              into the log (useful for troubleshooting a specific run). */}
          <ParamSection>
            <ParamSectionTitle>
              <span>{t('nomaflow.runParams.logLevel', 'Log level')}</span>
            </ParamSectionTitle>
            <ParamRow>
              <ParamKey>log_level</ParamKey>
              <Select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value as 'INFO' | 'DEBUG')}
              >
                <option value="INFO">
                  {t('nomaflow.runParams.logLevelInfo', 'INFO — row counts + progress markers')}
                </option>
                <option value="DEBUG">
                  {t('nomaflow.runParams.logLevelDebug', 'DEBUG — full SQL of every query')}
                </option>
              </Select>
            </ParamRow>
          </ParamSection>
          {/* Job-level params (shared across all steps). Rendered when the job carries
              any — operators that don't use job.params just won't see the section. */}
          {Object.keys(paramValues).length > 0 && (
            <ParamSection>
              <ParamSectionTitle>
                <span>{t('nomaflow.runParams.jobParams', 'Shared params (apply to every step)')}</span>
              </ParamSectionTitle>
              {Object.entries(paramValues).map(([k, v]) =>
                renderKwargRow(k, v, (nv) => setParamKey(k, nv)),
              )}
            </ParamSection>
          )}
          {pythonSteps.map((s) => (
            <ParamSection key={s.name}>
              <ParamSectionTitle>
                <span>{s.name}</span>
                <ParamSectionType>· {String((s as { callable?: unknown }).callable ?? '')}</ParamSectionType>
              </ParamSectionTitle>
              {Object.entries(stepValues[s.name] ?? {}).map(([k, v]) =>
                renderKwargRow(k, v, (nv) => setStepKey(s.name, k, nv)),
              )}
              {Object.keys(stepValues[s.name] ?? {}).length === 0 && (
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
