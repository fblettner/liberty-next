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
import { Play, Ban, Pencil, Plus, RefreshCw, Workflow, Clock, CalendarClock, ChevronDown, ChevronRight, Copy, Download, Trash2, Search, Star } from 'lucide-react'
import { api, apiDownload, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Tag, Mono, SpinnerRing, Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Checkbox, FileInput, Input, Select, SearchSelect, type SearchSelectOption, useModals } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { useAuth } from '../../auth/AuthContext'
import { nomaflowAccess } from '../../services/permissions'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { JobSummary, JobsListResponse, JobsParsedResponse, StepConfig } from './types'
import { STATE_TONE, relative, uploadJobFile } from './util'
import { applyPresetState } from './presetState'
import { toMulti } from '../../services/multiChoice'
import { MultiChoice } from '../../common/MultiChoice'

/** The tag that puts a job in the "Main jobs" group at the top of the list. */
const MAIN_TAG = 'main'
const MAIN_GROUP = '\u0000main'
import { RetentionPanel } from './RetentionPanel'
import { MissedPanel } from './MissedPanel'

const Toolbar = styled.div`
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px;
`
const ToolbarSpacer = styled.div`flex: 1;`
// Tag filter chip row — sits between the action toolbar and the job list. Each chip
// toggles a tag's membership in the active filter set; the union (any-match) of all
// active tags is shown. Active chip = blue tone; inactive = muted ghost.
const TagFilterClear = styled.button`
  font-size: ${fontSize.sm}; padding: 0 6px; height: 24px; cursor: pointer;
  background: transparent; border: none; color: ${colors.text.muted};
  &:hover { color: ${colors.text.primary}; }
`
const List = styled.div`display: flex; flex-direction: column; gap: 10px;`
// The compact list: one group per application, one line per job.
const Group = styled.div`flex: none; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; overflow: hidden;`
const GroupHead = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 12px; border: none; cursor: pointer;
  background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.base}; font-weight: 600; text-align: left;
  & .n { color: ${colors.text.muted}; font-weight: 400; font-size: ${fontSize.sm}; }
`
const JobRow = styled.div`
  display: grid; grid-template-columns: minmax(220px, 1.2fr) minmax(0, 2fr) 110px 150px 96px auto; align-items: center; gap: 12px;
  padding: 7px 12px; border-top: 1px solid ${colors.border}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:hover { background: var(--hover-subtle); }
  & .id { font-family: ${fonts.mono}; color: ${colors.text.primary}; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
  & .id:hover { color: ${colors.blue.main}; }
  & .desc { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .when { display: flex; flex-direction: column; line-height: 1.25; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .acts { display: flex; gap: 2px; justify-content: flex-end; }
  @media (max-width: 1100px) { grid-template-columns: minmax(180px, 1fr) 110px 96px auto; & .desc, & .sched { display: none; } }
`
const IconBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; padding: 0; cursor: pointer;
  border: 1px solid transparent; border-radius: ${radius.sm}; background: none; color: ${colors.text.secondary};
  &:hover:not(:disabled) { border-color: ${colors.border}; background: ${colors.bg.input}; color: ${colors.blue.main}; }
  &.danger:hover:not(:disabled) { color: ${colors.red.main}; }
  &:disabled { opacity: 0.4; cursor: default; }
`
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
const ParamRow = styled.label`
  /* minmax(0, 1fr) — NOT 1fr — so the value column can shrink below its content's
     intrinsic width. A native <select> with a long option (e.g. "(no saved presets…)")
     otherwise forces the whole modal to overflow horizontally and pushes controls
     (the Save-as-preset button) off the right edge. */
  display: grid; grid-template-columns: 200px minmax(0, 1fr); align-items: center; gap: 10px;
  font-size: ${fontSize.sm};
`
const ParamKey = styled.span`font-family: ${fonts.mono}; color: ${colors.text.secondary};`

// A real toggle styled as a pill — clearly clickable, clearly stateful.
const Toggle = styled.button<{ $on: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px;
  border-radius: ${radius.sm}; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  border: 1px solid ${({ $on }) => ($on ? colors.green.border : colors.border)};
  background: ${({ $on }) => ($on ? colors.green.bg : 'transparent')};
  color: ${({ $on }) => ($on ? colors.green.main : colors.text.muted)};
  &:disabled { opacity: 0.5; cursor: default; }
`

// Compact step list — one row per step (vs the heavier ParamSection treatment
// used for job-level params). Designed for jobs with 10+ steps: the list
// dominates the modal so single-line rows + a bulk select header keep "I just
// want to run one step" to a 2-click operation instead of N un-ticks.
const StepsListHeader = styled.div`
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 4px 2px;
`
const StepsListTitle = styled.div`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  display: flex; align-items: baseline; gap: 8px;
`
const StepsListCounter = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const StepsListActions = styled.div`display: flex; align-items: center; gap: 6px;`
const StepsList = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  display: flex; flex-direction: column;
`
const StepRow = styled.div<{ $expanded?: boolean }>`
  display: grid; grid-template-columns: 24px 1fr auto auto; align-items: center;
  gap: 10px; padding: 8px 10px; min-height: 36px;
  border-bottom: 1px solid ${colors.border};
  &:last-child { border-bottom: none; }
  background: ${({ $expanded }) => ($expanded ? colors.bg.card : 'transparent')};
`
const StepRowName = styled.span<{ $disabled?: boolean }>`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  color: ${({ $disabled }) => ($disabled ? colors.text.muted : colors.text.primary)};
`
const StepRowCallable = styled.span<{ $disabled?: boolean }>`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  opacity: ${({ $disabled }) => ($disabled ? 0.6 : 1)};
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
`
const StepRowExpander = styled.button`
  background: transparent; border: none; cursor: pointer; padding: 2px;
  color: ${colors.text.muted}; display: inline-flex; align-items: center;
  &:hover { color: ${colors.text.primary}; }
  &:disabled { visibility: hidden; }
`
const StepKwargsPanel = styled.div`
  padding: 8px 10px 12px 44px;  /* indent under the step row's name column */
  border-bottom: 1px solid ${colors.border};
  background: ${colors.bg.card};
  display: flex; flex-direction: column; gap: 6px;
  &:last-child { border-bottom: none; }
`
const LinkLikeButton = styled.button`
  background: transparent; border: none; padding: 2px 6px; cursor: pointer;
  font-size: ${fontSize.sm}; color: ${colors.text.muted};
  &:hover { color: ${colors.text.primary}; }
`

export default function JobsList() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  // what this user may do here (the server checks again): follow, edit, run a job, the policies
  const { user } = useAuth()
  const access = useMemo(() => nomaflowAccess(user), [user])
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

  // "Run with parameters" modal state. The modal carries the job-level params
  // (shared across all steps), the per-step config (so the per-step enable
  // toggle has a row per step + python steps render their op_kwargs), and the
  // job's saved log level. Submit sends a {params, op_kwargs, step_enabled,
  // log_level} body the runner merges with the saved config.
  const [paramModalJob, setParamModalJob] = useState<
    {
      job: JobSummary
      jobParams: Record<string, unknown>
      steps: StepConfig[]
      logLevel: 'WARNING' | 'INFO' | 'DEBUG'
      presets: import('./types').JobPreset[]
    } | null
  >(null)

  const _postRun = useCallback(async (
    jobId: string,
    body?: {
      params?: Record<string, unknown>
      op_kwargs?: Record<string, Record<string, unknown>>
      step_enabled?: Record<string, boolean>
      log_level?: 'WARNING' | 'INFO' | 'DEBUG'
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
        (body.step_enabled && Object.keys(body.step_enabled).length > 0) ||
        body.log_level !== undefined
      )
      await api.post(`/admin/jobs/${encodeURIComponent(jobId)}/run`, has ? body : undefined)
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [load])

  const runNow = useCallback(async (job: JobSummary) => {
    // Open the modal when the job carries job-level params, any python step
    // with non-empty op_kwargs, OR more than one step (so operators can pick
    // which steps to run via the enable toggles). Single-step no-kwargs jobs
    // still fire immediately — the modal would offer nothing actionable.
    setError(null); setBusyId(job.id)
    try {
      // the job alone (``job:<id>``) — reading the whole jobs file is for its editors
      const def = await api.get<JobsParsedResponse['jobs'][number]>(`/admin/jobs/${encodeURIComponent(job.id)}/definition`)
      const jobParams = (def?.params ?? {}) as Record<string, unknown>
      const allSteps = (def?.steps ?? [])
      const hasParams = Object.keys(jobParams).length > 0
      const hasPythonKwargs = allSteps.some((s) =>
        s.type === 'python' && s.op_kwargs && typeof s.op_kwargs === 'object'
          && Object.keys(s.op_kwargs as object).length > 0,
      )
      const hasMultipleSteps = allSteps.length > 1
      const hasFileLoad = allSteps.some((s) => s.type === 'file_load')
      const jobLogLevel: 'WARNING' | 'INFO' | 'DEBUG' =
        def?.log_level === 'DEBUG' ? 'DEBUG' : def?.log_level === 'WARNING' ? 'WARNING' : 'INFO'
      if (!hasParams && !hasPythonKwargs && !hasMultipleSteps && !hasFileLoad) {
        // No params + single no-kwargs step — quick-fire at the job's saved
        // log_level. Nothing the modal could usefully show (no kwargs to
        // edit, no step to selectively disable).
        setBusyId(null)
        await _postRun(job.id)
        return
      }
      setBusyId(null)
      setParamModalJob({
        job, jobParams, steps: allSteps, logLevel: jobLogLevel,
        presets: (def?.presets ?? []),
      })
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
      // the one job's ``enabled`` + its triggers, no full reload (POST /admin/jobs/{id}/enabled)
      await api.post(`/admin/jobs/${encodeURIComponent(job.id)}/enabled`, { enabled: !job.enabled })
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [load])

  const modals = useModals()

  const newJob = useCallback(async () => {
    navigate('/nomaflow/jobs/new')
  }, [navigate])

  // Duplicate a job — ask for the new id UP FRONT (the editor's id is read-only in
  // edit mode, so an auto ``<id>_copy`` used to be un-renameable), then clone the
  // source config under that id, disabled + unscheduled (a duplicate on the source's
  // cron would double-run its data), and open the editor.
  const duplicateJob = useCallback(async (job: JobSummary) => {
    setError(null)
    try {
      const parsed = await api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
      const src = parsed.jobs.find((j) => j.id === job.id)
      if (!src) throw new Error(`Source job ${job.id} not found in jobs.toml`)
      const existing = new Set(parsed.jobs.map((j) => j.id))
      // Suggest <id>_copy (bumped past collisions) but let the operator rename it.
      let suggested = `${job.id}_copy`
      for (let i = 2; existing.has(suggested); i += 1) suggested = `${job.id}_copy${i}`
      const answer = await modals.prompt({
        title: t('nomaflow.jobs.duplicateTitle'),
        message: t('nomaflow.jobs.duplicatePrompt'),
        defaultValue: suggested,
        placeholder: suggested,
      })
      const newId = answer?.trim()
      if (!newId) return  // cancelled or empty
      if (!/^[A-Za-z0-9_-]+$/.test(newId)) {
        setError(t('nomaflow.jobs.badId'))
        return
      }
      if (existing.has(newId)) {
        setError(t('nomaflow.jobs.idExists', { id: newId }))
        return
      }
      setBusyId(job.id)
      // Deep-clone via JSON round-trip — JobConfig is a pure JSON shape (no Dates /
      // functions / regex), so JSON.parse(JSON.stringify(...)) is safe + cheap.
      const cloned = { ...JSON.parse(JSON.stringify(src)), id: newId, enabled: false, schedule: undefined }
      // a preset's own cron fires even on a disabled job: the clone's presets must not run twice
      if (Array.isArray(cloned.presets)) cloned.presets = cloned.presets.map((p: { schedule?: string | null; enabled?: boolean }) => ({ ...p, schedule: undefined, enabled: false }))
      await api.put('/admin/config/jobs/parsed', { jobs: [...parsed.jobs, cloned] })
      await api.post('/admin/jobs/reload')
      navigate(`/nomaflow/jobs/${encodeURIComponent(newId)}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [navigate, modals, t])

  // Delete a job — confirm, then rewrite jobs.toml without it + reload. No dedicated
  // endpoint needed: PUT /admin/config/jobs/parsed replaces the whole list, so removing
  // the entry deletes it. Run history (nomaflow_job_runs) is retention-managed, not touched.
  const deleteJob = useCallback(async (job: JobSummary) => {
    const ok = await modals.confirm({
      title: t('nomaflow.jobs.deleteTitle'),
      message: t('nomaflow.jobs.deletePrompt', {
        id: job.id,
      }),
      variant: 'danger',
    })
    if (!ok) return
    setBusyId(job.id); setError(null)
    try {
      const parsed = await api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
      const next = parsed.jobs.filter((j) => j.id !== job.id)
      await api.put('/admin/config/jobs/parsed', { jobs: next })
      await api.post('/admin/jobs/reload')
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusyId(null) }
  }, [modals, load, t])

  // Tag filter — operators pin one or more tag chips from the toolbar; matching jobs
  // are those whose ``tags`` contain ANY of the selected tags (union, not intersection
  // — picking ``nomasx1`` + ``security`` shows everything tagged either, matching the
  // "narrow my view" mental model). Empty selection = show everything.
  const FILTER_KEY = 'liberty.nomaflow.jobs.filters'
  const saved = useMemo(() => { try { return JSON.parse(localStorage.getItem(FILTER_KEY) ?? '{}') as Record<string, string> } catch { return {} } }, [])
  const [q, setQ] = useState(saved.q ?? '')
  const [status, setStatus] = useState(saved.status ?? '')
  const [appFilter, setAppFilter] = useState(saved.app ?? '')
  const [tagFilter, setTagFilter] = useState(saved.tag ?? '')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  useEffect(() => { try { localStorage.setItem(FILTER_KEY, JSON.stringify({ q, status, app: appFilter, tag: tagFilter })) } catch { /* ignore */ } }, [q, status, appFilter, tagFilter])
  const { connectors: wsConnectors } = useWorkspace()
  // The application a job belongs to: its first tag naming a connector (nomasx1, nomajde…), else
  // the prefix of its id; framework jobs (no app) land in "Framework".
  const appOf = useCallback((j: JobSummary): string => {
    const names = new Set((wsConnectors ?? []).map((c) => c.name))
    const tagged = j.tags.find((tg) => names.has(tg))
    if (tagged) return tagged
    const prefix = j.id.split('-')[0]
    return names.has(prefix) ? prefix : 'framework'
  }, [wsConnectors])
  const allTags = useMemo<string[]>(() => {
    if (!jobs) return []
    const acc = new Set<string>()
    for (const j of jobs) for (const tg of j.tags) acc.add(tg)
    return [...acc].sort()
  }, [jobs])

  const sorted = useMemo(() => {
    if (!jobs) return null
    const needle = q.trim().toLowerCase()
    return [...jobs].sort((a, b) => a.id.localeCompare(b.id)).filter((j) => {
      if (tagFilter && !j.tags.includes(tagFilter)) return false
      if (appFilter && appOf(j) !== appFilter) return false
      if (status === 'enabled' && !j.enabled) return false
      if (status === 'disabled' && j.enabled) return false
      if (status === 'scheduled' && !(j.enabled && j.next_run)) return false
      if (status === 'running' && !j.in_flight) return false
      if (status === 'failed' && j.last_run?.state !== 'FAILED') return false
      if (needle && !`${j.id} ${j.description ?? ''} ${j.tags.join(' ')}`.toLowerCase().includes(needle)) return false
      return true
    })
  }, [jobs, q, status, appFilter, tagFilter, appOf])
  const groups = useMemo(() => {
    const m = new Map<string, JobSummary[]>()
    // A job tagged `main` (the collect-all, the scheduled reports…) goes in the "Main jobs" group
    // on top instead of its application's group; the application filter still applies to it.
    for (const j of sorted ?? []) { const a = j.tags.includes(MAIN_TAG) ? MAIN_GROUP : appOf(j); m.set(a, [...(m.get(a) ?? []), j]) }
    const rank = (a: string) => (a === MAIN_GROUP ? 0 : a === 'framework' ? 2 : 1)
    return [...m.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
  }, [sorted, appOf])
  const appOptions = useMemo<SearchSelectOption[]>(() => [...new Set((jobs ?? []).map(appOf))].map((a) => ({ value: a, label: a === 'framework' ? t('nomaflow.jobs.framework') : a })), [jobs, appOf, t])
  const filtersOn = !!(q || status || appFilter || tagFilter)

  // Export — a minimal ZIP with this job's [[jobs]] entry + a MANIFEST.md (referenced queries /
  // connectors are not bundled; Liberty Next → Deploy → Package ships the config slice).
  const exportJob = async (job: JobSummary) => {
    try {
      await apiDownload('/admin/export-job', `nomaflow-job-${job.id}.zip`, { json: { id: job.id } })
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  const header = (
    <PageLayout
      icon={<Workflow size={18} />}
      title={t('nomaflow.jobs.title')}
      description={t('nomaflow.jobs.subtitle')}
    >
      <Toolbar>
        {access.edit && (
          <Button $variant="primary" $size="sm" onClick={newJob}>
            <Plus size={14} /> {t('nomaflow.jobs.new')}
          </Button>
        )}
        <Button $variant="ghost" $size="sm" onClick={() => navigate('/nomaflow/schedule')}>
          <CalendarClock size={14} /> {t('nomaflow.jobs.scheduleView')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={load}>
          <RefreshCw size={14} /> {t('common.reload')}
        </Button>
        <ToolbarSpacer />
      </Toolbar>
      <Toolbar>
        <div style={{ position: 'relative', flex: '1 1 260px', maxWidth: 380 }}>
          <Search size={14} style={{ position: 'absolute', left: 9, top: 9, color: colors.text.muted }} />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('nomaflow.jobs.search')} style={{ paddingLeft: 28 }} />
        </div>
        <div style={{ width: 170 }}>
          <SearchSelect value={appFilter} onChange={setAppFilter} options={appOptions} anyLabel={t('nomaflow.jobs.allApps')} placeholder={t('nomaflow.jobs.allApps')} sort />
        </div>
        <div style={{ width: 170 }}>
          <SearchSelect value={status} onChange={setStatus} anyLabel={t('nomaflow.jobs.anyStatus')} placeholder={t('nomaflow.jobs.anyStatus')}
            options={['enabled', 'disabled', 'scheduled', 'running', 'failed'].map((v) => ({ value: v, label: t(`nomaflow.jobs.status.${v}`) }))} />
        </div>
        <div style={{ width: 170 }}>
          <SearchSelect value={tagFilter} onChange={setTagFilter} options={allTags.map((tg) => ({ value: tg, label: tg }))} anyLabel={t('nomaflow.jobs.anyTag')} placeholder={t('nomaflow.jobs.anyTag')} sort />
        </div>
        {filtersOn && (
          <TagFilterClear type="button" onClick={() => { setQ(''); setStatus(''); setAppFilter(''); setTagFilter('') }}>
            {t('nomaflow.jobs.clearFilters')}
          </TagFilterClear>
        )}
        <ToolbarSpacer />
        {sorted && jobs && <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('nomaflow.jobs.shown', { n: sorted.length, total: jobs.length })}</span>}
      </Toolbar>
      {/* Retention panel — collapsible; surfaces the run-history cleanup policy +
          the last sweep's result + a Clean-now button. `onChanged` re-fetches
          the job list because a sweep may have deleted the latest run row that
          a card was displaying as last_run. */}
      {access.policies && <RetentionPanel onChanged={load} />}
      {access.policies && <MissedPanel onChanged={load} />}
      {error && <Banner $tone="error" style={{ marginBottom: 12 }}>{error}</Banner>}
      {sorted == null && !error ? <Centered /> : null}
      {sorted && sorted.length === 0 && (
        <Empty>{t('nomaflow.jobs.empty')}</Empty>
      )}
      {sorted && sorted.length > 0 && (
        <List>
          {groups.map(([app, list]) => {
            const open = !collapsed.has(app)
            return (
              <Group key={app}>
                <GroupHead type="button" onClick={() => setCollapsed((c) => { const n = new Set(c); if (n.has(app)) n.delete(app); else n.add(app); return n })}>
                  {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {app === MAIN_GROUP ? <><Star size={13} /> {t('nomaflow.jobs.mainJobs')}</> : app === 'framework' ? t('nomaflow.jobs.framework') : app}
                  <span className="n">{list.length}</span>
                </GroupHead>
                {open && list.map((job) => {
                  const busy = busyId === job.id
                  return (
                    <JobRow key={job.id}>
                      <span className="id" title={job.id} onClick={access.edit ? () => navigate(`/nomaflow/jobs/${encodeURIComponent(job.id)}`) : undefined}>{job.id}</span>
                      <span className="desc" title={job.description ?? ''}>{job.description}</span>
                      <span>
                        {job.last_run ? (
                          <RunLink onClick={() => navigate(`/nomaflow/runs/${encodeURIComponent(job.last_run!.run_id)}`)} title={`${t('nomaflow.jobs.viewRun')} · ${job.last_run.finished_at ? new Date(job.last_run.finished_at).toLocaleString() : ''}`}>
                            <Tag $tone={STATE_TONE[job.last_run.state]}>{job.last_run.state}</Tag>
                          </RunLink>
                        ) : <span style={{ color: colors.text.muted }}>—</span>}
                      </span>
                      <span className="when sched" title={job.next_run ? new Date(job.next_run).toLocaleString() : ''}>
                        <span><Clock size={11} style={{ verticalAlign: '-1px' }} /> {job.schedule ? <Mono>{job.schedule}</Mono> : t('nomaflow.jobs.manualOnly')}</span>
                        {job.enabled && job.next_run && <span>{t('nomaflow.jobs.nextRun')} {relative(job.next_run)}</span>}
                      </span>
                      <Toggle $on={job.enabled} disabled={busy || !access.edit} onClick={() => toggleEnabled(job)} title={t(job.enabled ? 'nomaflow.jobs.disable' : 'nomaflow.jobs.enable')}>
                        {busy ? <SpinnerRing size={12} thickness={2} /> : null}
                        {job.enabled ? t('nomaflow.jobs.enabled') : t('nomaflow.jobs.disabled')}
                      </Toggle>
                      <span className="acts">
                        {!access.run(job.id) ? null : job.in_flight
                          ? <IconBtn className="danger" type="button" disabled={busy} onClick={() => cancelRun(job)} title={t('nomaflow.jobs.cancel')}><Ban size={14} /></IconBtn>
                          : <IconBtn type="button" disabled={busy} onClick={() => runNow(job)} title={t('nomaflow.jobs.runNow')}>{busy ? <SpinnerRing size={12} thickness={2} /> : <Play size={14} />}</IconBtn>}
                        {access.edit && <>
                          <IconBtn type="button" disabled={busy} onClick={() => navigate(`/nomaflow/jobs/${encodeURIComponent(job.id)}`)} title={t('common.edit')}><Pencil size={14} /></IconBtn>
                          <IconBtn type="button" disabled={busy} onClick={() => duplicateJob(job)} title={t('nomaflow.jobs.duplicate')}><Copy size={14} /></IconBtn>
                        </>}
                        {/* the export is a deployment package — Liberty Next → Deploy, superuser */}
                        {access.policies && <IconBtn type="button" disabled={busy} onClick={() => void exportJob(job)} title={t('nomaflow.jobs.export')}><Download size={14} /></IconBtn>}
                        {access.edit && <IconBtn className="danger" type="button" disabled={busy} onClick={() => deleteJob(job)} title={t('nomaflow.jobs.delete')}><Trash2 size={14} /></IconBtn>}
                      </span>
                    </JobRow>
                  )
                })}
              </Group>
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
          steps={paramModalJob.steps}
          jobLogLevel={paramModalJob.logLevel}
          presets={paramModalJob.presets}
          onCancel={() => setParamModalJob(null)}
          onSubmit={async (body) => {
            setParamModalJob(null)
            await _postRun(paramModalJob.job.id, body)
          }}
          onSavePreset={!access.edit ? undefined : async (preset) => {
            // Read jobs.toml fresh, splice the new preset into THIS job's
            // ``presets`` array (replacing any same-name entry — operators
            // typing the same name expect to update, not duplicate), write
            // back, reload. The modal stays open with the updated catalog.
            const parsed = await api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
            const updated = parsed.jobs.map((j) => {
              if (j.id !== paramModalJob.job.id) return j
              // Preserve a schedule/timezone the operator attached in the editor — the modal
              // only edits params/op_kwargs/log_level/step_enabled, so a same-name re-save
              // must not drop the preset's cron.
              const prior = (j.presets ?? []).find((p) => p.name === preset.name)
              const presets = (j.presets ?? []).filter((p) => p.name !== preset.name)
              const kept = prior?.schedule
                ? { ...preset, schedule: prior.schedule, timezone: prior.timezone ?? null }
                : preset
              return { ...j, presets: [...presets, kept] }
            })
            await api.put('/admin/config/jobs/parsed', { jobs: updated })
            await api.post('/admin/jobs/reload')
            // Refresh the modal's preset list so the new entry appears in
            // the dropdown without re-opening the modal.
            const refreshed = updated.find((j) => j.id === paramModalJob.job.id)
            setParamModalJob((cur) => cur ? { ...cur, presets: refreshed?.presets ?? [] } : cur)
          }}
        />
      )}
    </>
  )
}


// "Run with parameters" — modal that lets the operator override op_kwargs for one fire of
// a job without editing jobs.toml. Sections:
//   * Log level (per-fire override of jobs.toml log_level)
//   * Shared params (the job-level kwargs merged under every step)
//   * One section per step with its enable toggle + (for python steps) its op_kwargs
// On submit, sends only the diff vs. saved as a `{params, op_kwargs, step_enabled,
// log_level}` body — the runner merges with the saved config (layer order:
// job.params → step.op_kwargs → params override → per-step op_kwargs override,
// each layer winning over the previous; step_enabled override wins over
// step.enabled in jobs.toml).
//
// Inputs are type-aware: boolean → Checkbox; number → number input; string → text input;
// and a key ending in `_connector` (or named exactly `connector`) → SearchSelect of the
// available connectors loaded from the workspace. Saves typos vs free-text.
function RunWithParamsModal({
  job, jobParams, steps, jobLogLevel, presets, onCancel, onSubmit, onSavePreset,
}: {
  job: JobSummary
  jobParams: Record<string, unknown>
  /** Every step of the job — python steps render their op_kwargs in addition
   *  to the enable toggle; sql_copy / sql_query / ldap_sync / http steps show
   *  only the toggle (their typed fields are configured from the job editor,
   *  not per-fire). */
  steps: StepConfig[]
  /** The job's saved log_level from jobs.toml (defaults to 'INFO'). The dropdown
   *  is seeded from this; on submit we only send log_level when the operator
   *  picked a value DIFFERENT from this saved default. */
  jobLogLevel: 'WARNING' | 'INFO' | 'DEBUG'
  /** Named presets saved on the job — surfaced as a dropdown above the form.
   *  Empty array hides the dropdown. Picking a preset resets the dialog to the
   *  job's saved values, then applies the preset (presetState.ts). */
  presets: import('./types').JobPreset[]
  onCancel: () => void
  onSubmit: (body: {
    params?: Record<string, unknown>
    op_kwargs?: Record<string, Record<string, unknown>>
    step_enabled?: Record<string, boolean>
    log_level?: 'WARNING' | 'INFO' | 'DEBUG'
  }) => Promise<void> | void
  /** "Save current state as a preset" — parent persists into jobs.toml + reloads.
   *  The current modal state is captured as a JobPreset (log_level + params +
   *  op_kwargs + step_enabled diff-vs-defaults). Promise resolves when the save
   *  + reload land; rejects with a string the modal renders as an error. */
  /** Absent for who may run the job but not edit it — no "save as preset" then. */
  onSavePreset?: (preset: import('./types').JobPreset) => Promise<void>
}) {
  const { t } = useTranslation()
  const modals = useModals()
  const { connectors } = useWorkspace()
  const connectorOptions = useMemo<SearchSelectOption[]>(
    () => (connectors ?? []).map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [connectors],
  )

  // Working copies — seed from saved values, diff on submit so we only send what changed.
  const initialStepKwargs = useMemo(() => {
    const out: Record<string, Record<string, unknown>> = {}
    for (const s of steps) {
      out[s.name] = { ...((s.op_kwargs as Record<string, unknown>) ?? {}) }
    }
    return out
  }, [steps])
  // step.enabled defaults to true in the schema; we treat undefined the same
  // way so a missing field doesn't surprise the operator into a disabled step.
  const initialStepEnabled = useMemo(() => {
    const out: Record<string, boolean> = {}
    for (const s of steps) out[s.name] = s.enabled !== false
    return out
  }, [steps])
  const [stepValues, setStepValues] = useState<Record<string, Record<string, unknown>>>(initialStepKwargs)
  const [stepEnabled, setStepEnabled] = useState<Record<string, boolean>>(initialStepEnabled)
  // file_load steps: a file picked here is uploaded on Run and handed to the step as its
  // per-fire `file` kwarg (it wins over the step's configured path / watched folder).
  const [stepFiles, setStepFiles] = useState<Record<string, File | null>>({})
  const [uploadError, setUploadError] = useState<string | null>(null)
  // Which steps are expanded to show their kwargs panel. Default: collapsed
  // (the kwargs are second-order — the toggle is the primary action). The
  // first python step with non-empty kwargs auto-expands so an operator who
  // came here specifically to edit kwargs sees something immediately.
  const firstExpandable = steps.find((s) =>
    s.type === 'python' && Object.keys(initialStepKwargs[s.name] ?? {}).length > 0,
  )?.name
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>(
    firstExpandable ? { [firstExpandable]: true } : {},
  )
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({ ...jobParams })
  const [logLevel, setLogLevel] = useState<'WARNING' | 'INFO' | 'DEBUG'>(jobLogLevel)
  const [busy, setBusy] = useState(false)

  const setStepKey = (stepName: string, key: string, v: unknown) => {
    setStepValues((prev) => ({ ...prev, [stepName]: { ...prev[stepName], [key]: v } }))
  }
  const setParamKey = (key: string, v: unknown) => {
    setParamValues((prev) => ({ ...prev, [key]: v }))
  }
  const setStepEnable = (stepName: string, v: boolean) => {
    setStepEnabled((prev) => ({ ...prev, [stepName]: v }))
  }
  const toggleStepExpanded = (stepName: string) => {
    setExpandedSteps((prev) => ({ ...prev, [stepName]: !prev[stepName] }))
  }
  // Bulk select / clear — fixes the "I want to run just USERS" UX where the
  // operator would otherwise tick-untick N-1 boxes. "All" sets every step
  // enabled; "None" turns them all off (operator then ticks the one they want).
  const enabledCount = Object.values(stepEnabled).filter(Boolean).length
  const selectAllSteps = () => {
    setStepEnabled(Object.fromEntries(steps.map((s) => [s.name, true])))
  }
  const selectNoneSteps = () => {
    setStepEnabled(Object.fromEntries(steps.map((s) => [s.name, false])))
  }

  // ── presets ────────────────────────────────────────────────────────────
  // Picking a preset resets the dialog to the job's saved values, then applies the preset
  // (presetState.ts) — the same reading the scheduler and the user's page make of it. The
  // dialog stays editable after; "(none)" goes back to the saved values.
  const [selectedPreset, setSelectedPreset] = useState<string>('')
  const [savePresetBusy, setSavePresetBusy] = useState(false)
  const [savePresetError, setSavePresetError] = useState<string | null>(null)
  const applyPreset = (presetName: string) => {
    setSelectedPreset(presetName)
    const next = applyPresetState(
      { params: { ...jobParams }, stepValues: initialStepKwargs, stepEnabled: initialStepEnabled, logLevel: jobLogLevel },
      presetName ? presets.find((pr) => pr.name === presetName) : null,
    )
    setParamValues(next.params)
    setStepValues(next.stepValues)
    setStepEnabled(next.stepEnabled)
    setLogLevel(next.logLevel)
  }
  const handleSavePreset = async () => {
    const rawName = await modals.prompt({
      title: t('nomaflow.runParams.savePresetTitle'),
      message: t('nomaflow.runParams.savePresetPrompt'),
      placeholder: t('nomaflow.runParams.savePresetPlaceholder') as string,
      submitLabel: t('common.save'),
    })
    const name = rawName?.trim()
    if (!name) return
    setSavePresetBusy(true); setSavePresetError(null)
    try {
      // Capture EVERYTHING currently in the modal — full snapshot per the
      // operator's choice ("save everything currently in the modal"). The
      // diff-vs-defaults logic is for the per-fire payload, not for presets;
      // an operator saving a preset wants to lock in exactly what they see.
      const preset: import('./types').JobPreset = {
        name,
        log_level: logLevel,
        params: { ...paramValues },
        op_kwargs: Object.fromEntries(
          steps.map((s) => [s.name, { ...(stepValues[s.name] ?? {}) }]).filter(([, kw]) => Object.keys(kw as object).length > 0),
        ),
        step_enabled: { ...stepEnabled },
      }
      await onSavePreset?.(preset)
      setSelectedPreset(name)
    } catch (e) {
      setSavePresetError(e instanceof Error ? e.message : String(e))
    } finally {
      setSavePresetBusy(false)
    }
  }

  const submit = async () => {
    setUploadError(null)
    // Per-step kwargs diff: only the kwargs that changed end up in the payload.
    const stepOverrides: Record<string, Record<string, unknown>> = {}
    for (const s of steps) {
      const before = initialStepKwargs[s.name] ?? {}
      const after = stepValues[s.name] ?? {}
      const diff: Record<string, unknown> = {}
      for (const k of Object.keys(after)) {
        if (!Object.is(after[k], before[k])) diff[k] = after[k]
      }
      if (Object.keys(diff).length > 0) stepOverrides[s.name] = diff
    }
    // Per-step enable diff: only toggles the operator changed.
    const stepEnabledDiff: Record<string, boolean> = {}
    for (const s of steps) {
      if (stepEnabled[s.name] !== initialStepEnabled[s.name]) {
        stepEnabledDiff[s.name] = stepEnabled[s.name]
      }
    }
    // Job-level params diff.
    const paramsDiff: Record<string, unknown> = {}
    for (const k of Object.keys(paramValues)) {
      if (!Object.is(paramValues[k], jobParams[k])) paramsDiff[k] = paramValues[k]
    }
    const body: {
      params?: Record<string, unknown>
      op_kwargs?: Record<string, Record<string, unknown>>
      step_enabled?: Record<string, boolean>
      log_level?: 'WARNING' | 'INFO' | 'DEBUG'
    } = {}
    if (Object.keys(paramsDiff).length > 0) body.params = paramsDiff
    if (Object.keys(stepOverrides).length > 0) body.op_kwargs = stepOverrides
    if (Object.keys(stepEnabledDiff).length > 0) body.step_enabled = stepEnabledDiff
    // Only send log_level when it differs from the job's saved default — keeps
    // the payload minimal + lets the runner fall back to job.log_level when
    // nothing's specified (matches the operator's intent: "I left the dropdown
    // alone, so use what's in jobs.toml").
    if (logLevel !== jobLogLevel) body.log_level = logLevel
    setBusy(true)
    try {
      // Uploads first: a failed upload leaves the run unfired.
      for (const [stepName, file] of Object.entries(stepFiles)) {
        if (!file) continue
        const up = await uploadJobFile(job.id, file)
        body.op_kwargs = { ...(body.op_kwargs ?? {}), [stepName]: { ...(body.op_kwargs?.[stepName] ?? {}), file: up.path } }
      }
      await onSubmit(body)
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e))
      return
    } finally {
      setBusy(false)
    }
  }

  // Render one row per kwarg, typed input based on value + key. Extracted so both the
  // job-level params section and each step's section share the rendering logic.
  const renderKwargRow = (key: string, v: unknown, setter: (v: unknown) => void, step: string | null = null) => {
    // _connector / connector → connector picker (saves typos vs free text).
    const isConnectorKey = key === 'connector' || key.endsWith('_connector')
    // A parameter the job declares a choice list for (user_params: the applications of an app,
    // a language…) → a dropdown; a number keeps its type.
    const declared = (job.user_params ?? []).find((p) => p.name === key && (p.step ?? null) === step && p.choices.length > 0)
    return (
      <ParamRow key={key}>
        <ParamKey title={declared?.label}>{key}</ParamKey>
        {declared?.multiple ? (
          <MultiChoice value={toMulti(v)} choices={declared.choices} emptyHint={t('jobrun.multiNone')}
            onChange={(nv) => setter(Array.isArray(nv) && nv.length === 0 ? (step ? null : jobParams[key] ?? null) : nv)} />
        ) : declared ? (
          <SearchSelect sort
            value={v == null ? '' : String(v)}
            options={declared.choices}
            onChange={(nv) => setter(typeof v === 'number' && nv !== '' && Number.isFinite(Number(nv)) ? Number(nv) : nv)}
          />
        ) : isConnectorKey ? (
          <SearchSelect sort
            value={v == null ? '' : String(v)}
            options={connectorOptions}
            onChange={setter}
            placeholder={t('nomaflow.runParams.pickConnector')}
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
          {t('nomaflow.runParams.title')} ·{' '}
          <Mono style={{ color: colors.text.muted, fontWeight: 400 }}>{job.id}</Mono>
        </ModalHeader>
        <ModalBody>
          <div style={{ color: colors.text.muted, fontSize: fontSize.sm }}>
            {t('nomaflow.runParams.hint')}
          </div>
          {/* Presets — named snapshots saved by operators. Always shown (even
              when the job has none yet) so the "Save as preset…" button is
              discoverable: an operator who tunes the form three times in a row
              learns they can lock that combo in. Picking a preset layers its
              values onto the form (doesn't replace untouched fields). */}
          <ParamSection>
              <ParamSectionTitle>
                <span>{t('nomaflow.runParams.presets')}</span>
              </ParamSectionTitle>
              <ParamRow>
                <ParamKey>{t('nomaflow.runParams.presetPick')}</ParamKey>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 }}>
                  <Select
                    value={selectedPreset}
                    onChange={(e) => applyPreset(e.target.value)}
                    disabled={presets.length === 0}
                    style={{ flex: 1, minWidth: 0 }}
                  >
                    <option value="">
                      {presets.length === 0
                        ? t('nomaflow.runParams.noPresets')
                        : t('nomaflow.runParams.pickPreset')}
                    </option>
                    {presets.map((p) => (
                      <option key={p.name} value={p.name}>{p.name}</option>
                    ))}
                  </Select>
                  {onSavePreset && <Button
                    $variant="ghost" $size="sm"
                    onClick={handleSavePreset}
                    disabled={savePresetBusy}
                    style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
                    title={t('nomaflow.runParams.savePresetTitle')}
                  >
                    {savePresetBusy ? <SpinnerRing size={12} thickness={2} /> : null}
                    {t('nomaflow.runParams.savePresetBtn')}
                  </Button>}
                </div>
              </ParamRow>
              {savePresetError && (
                <Banner $tone="error">{savePresetError}</Banner>
              )}
            </ParamSection>
          {/* Log-level override — per-fire-only. Defaults to the job's saved
              log_level from jobs.toml; submit only includes the field when the
              operator changed it. DEBUG emits the full SQL of every run_query
              into the log (useful for troubleshooting a specific run). */}
          <ParamSection>
            <ParamSectionTitle>
              <span>{t('nomaflow.runParams.logLevel')}</span>
            </ParamSectionTitle>
            <ParamRow>
              <ParamKey>log_level</ParamKey>
              <Select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value as 'WARNING' | 'INFO' | 'DEBUG')}
              >
                <option value="WARNING">
                  {t('nomaflow.runParams.logLevelWarning')}
                </option>
                <option value="INFO">
                  {t('nomaflow.runParams.logLevelInfo')}
                </option>
                <option value="DEBUG">
                  {t('nomaflow.runParams.logLevelDebug')}
                </option>
              </Select>
            </ParamRow>
          </ParamSection>
          {/* Job-level params (shared across all steps). Rendered when the job carries
              any — operators that don't use job.params just won't see the section. */}
          {Object.keys(paramValues).length > 0 && (
            <ParamSection>
              <ParamSectionTitle>
                <span>{t('nomaflow.runParams.jobParams')}</span>
              </ParamSectionTitle>
              {Object.entries(paramValues).map(([k, v]) =>
                renderKwargRow(k, v, (nv) => setParamKey(k, nv)),
              )}
            </ParamSection>
          )}
          {/* Steps — compact list. Each row is one line: [checkbox] STEP_NAME
              callable [chevron]. Bulk select/none header lets the operator
              flip the whole set at once ("run only USERS" = None + tick one).
              Steps with kwargs (python with non-empty op_kwargs) expand to
              show the kwarg editor below the row; non-python steps + python
              with no kwargs have nothing to expand. */}
          <div>
            <StepsListHeader>
              <StepsListTitle>
                <span>{t('nomaflow.runParams.steps')}</span>
                <StepsListCounter>
                  {t('nomaflow.runParams.stepsCount',
                    { enabled: enabledCount, total: steps.length })}
                </StepsListCounter>
              </StepsListTitle>
              <StepsListActions>
                <LinkLikeButton
                  onClick={selectAllSteps}
                  disabled={enabledCount === steps.length}
                >
                  {t('nomaflow.runParams.selectAll')}
                </LinkLikeButton>
                <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>·</span>
                <LinkLikeButton
                  onClick={selectNoneSteps}
                  disabled={enabledCount === 0}
                >
                  {t('nomaflow.runParams.selectNone')}
                </LinkLikeButton>
              </StepsListActions>
            </StepsListHeader>
            <StepsList>
              {steps.map((s) => {
                const isPython = s.type === 'python'
                const isFileLoad = s.type === 'file_load'
                const callable = String((s as { callable?: unknown }).callable ?? '')
                const configuredSource = isFileLoad
                  ? String((s as { path?: unknown }).path || (s as { watch_dir?: unknown }).watch_dir || '')
                  : ''
                const subtitle = isPython ? callable : s.type
                const kwargs = stepValues[s.name] ?? {}
                const hasKwargs = Object.keys(kwargs).length > 0
                const expandable = isPython && hasKwargs
                const isExpanded = !!expandedSteps[s.name]
                const enabled = stepEnabled[s.name] ?? true
                return (
                  <div key={s.name}>
                    <StepRow $expanded={isExpanded && expandable}>
                      <Checkbox
                        checked={enabled}
                        onChange={(checked) => setStepEnable(s.name, checked)}
                      />
                      <StepRowName $disabled={!enabled}>{s.name}</StepRowName>
                      <StepRowCallable $disabled={!enabled}>{subtitle}</StepRowCallable>
                      <StepRowExpander
                        type="button"
                        onClick={() => toggleStepExpanded(s.name)}
                        disabled={!expandable}
                        title={expandable
                          ? (isExpanded
                              ? t('nomaflow.runParams.collapseKwargs')
                              : t('nomaflow.runParams.expandKwargs'))
                          : ''}
                      >
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </StepRowExpander>
                    </StepRow>
                    {expandable && isExpanded && (
                      <StepKwargsPanel>
                        {Object.entries(kwargs).map(([k, v]) =>
                          renderKwargRow(k, v, (nv) => setStepKey(s.name, k, nv), s.name),
                        )}
                      </StepKwargsPanel>
                    )}
                    {isFileLoad && enabled && (
                      <StepKwargsPanel>
                        <ParamRow>
                          <ParamKey>{t('nomaflow.runParams.file')}</ParamKey>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                            <FileInput value={stepFiles[s.name] ?? null} accept=".xlsx,.xlsm,.csv,.txt,.tsv"
                              onChange={(file) => setStepFiles((prev) => ({ ...prev, [s.name]: file }))} />
                            <span style={{ fontSize: fontSize.micro, color: colors.text.muted }}>
                              {configuredSource
                                ? t('nomaflow.runParams.fileHint', { source: configuredSource })
                                : t('nomaflow.runParams.fileRequired')}
                            </span>
                          </div>
                        </ParamRow>
                      </StepKwargsPanel>
                    )}
                  </div>
                )
              })}
            </StepsList>
          </div>
        </ModalBody>
        <ModalFooter>
          {uploadError && <Banner $tone="error" style={{ flex: 1 }}>{uploadError}</Banner>}
          <Button $variant="ghost" onClick={onCancel} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button $variant="primary" onClick={submit} disabled={busy}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Play size={13} />}{' '}
            {t('nomaflow.runParams.run')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
