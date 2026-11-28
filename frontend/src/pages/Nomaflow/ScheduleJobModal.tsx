// "Schedule a job" — one dialog for one schedule of a job: its name, what it runs with (one of the job's presets,
// or the job as it is), its environment, its own values of the job's shared parameters, when it runs and whether it
// is active. Opened from the Schedule page and the Jobs page (it saves the jobs file), or from the job editor (it
// hands the job back, which the editor saves with the rest). The logic is services/jobSchedules.ts.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { CalendarClock, Trash2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Centered, Checkbox, Input, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SearchSelect, TimezoneSelect, type SearchSelectOption } from '../../common'
import { colors, fontSize } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { jobName } from '../../services/jobGroups'
import { baseParams, newScheduleName, paramText, paramValue, scheduleOf, scheduleProblem, withSchedule, withoutSchedule } from '../../services/jobSchedules'
import ScheduleField from './ScheduleField'
import type { JobConfig, JobSummary, JobsListResponse, JobsParsedResponse } from './types'

const Field = styled.div`display: flex; flex-direction: column; gap: 4px;`
const Label = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const Hint = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const Pair = styled.div`display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; @media (max-width: 640px) { grid-template-columns: 1fr; }`
const ParamGrid = styled.div`display: grid; grid-template-columns: minmax(140px, 220px) minmax(0, 1fr); align-items: center; gap: 8px 12px;`

/** The schedule picker's entry for a schedule to create. */
const NEW = '\u0000new'

export default function ScheduleJobModal({ initial, inEditor, onClose, onSaved }: {
  /** The job and the schedule opened (a line of the Schedule page), or just a job; nothing = the user picks. */
  initial?: { jobId?: string; name?: string | null; create?: boolean }
  /** Opened from the job editor: the job as it is being edited, and where to hand it back (nothing is saved here). */
  inEditor?: { job: JobConfig; onApply: (job: JobConfig) => void }
  onClose: () => void
  onSaved?: () => void
}) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  const [loaded, setLoaded] = useState<JobConfig[] | null>(null)
  const editedJob = inEditor?.job
  const jobs = useMemo(() => (editedJob ? [editedJob] : loaded), [editedJob, loaded])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [jobId, setJobId] = useState(inEditor?.job.id ?? initial?.jobId ?? '')
  const [was, setWas] = useState<string | null>(initial?.name ?? (initial?.create ? NEW : null))     // the schedule being changed; NEW = one to create, null = not decided yet
  const [name, setName] = useState('')
  const [preset, setPreset] = useState('')
  const [pool, setPool] = useState('')
  const [timezone, setTimezone] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})        // the shared parameters, as typed
  const [schedule, setSchedule] = useState<string | null>(null)
  const [enabled, setEnabled] = useState(true)
  const [summaries, setSummaries] = useState<JobSummary[]>([])            // for a parameter's label and choices

  useEffect(() => {
    if (!inEditor) api.get<JobsParsedResponse>('/admin/config/jobs/parsed').then((r) => setLoaded(r.jobs)).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
    api.get<JobsListResponse>('/admin/jobs').then((r) => setSummaries(r.jobs)).catch(() => { /* the parameters are then shown by their names */ })
  }, [inEditor])

  const job = useMemo(() => (jobs ?? []).find((j) => j.id === jobId) ?? null, [jobs, jobId])
  // a job opened without naming a schedule: its first one when it has some, else a new one
  useEffect(() => { if (job && was == null) setWas(job.schedules?.[0]?.name ?? NEW) }, [job, was])

  // what the file holds for the schedule picked — or a new one's starting values — shown, then the user's to change
  useEffect(() => {
    if (!job || was == null) return
    const now = was === NEW ? null : scheduleOf([job], job.id, was)
    const under = baseParams(job, now?.preset)
    setName(now?.name ?? newScheduleName(job))
    setPreset(now?.preset ?? '')
    setPool(now?.pool ?? '')
    setTimezone(now?.timezone ?? '')
    setSchedule(now?.schedule ?? null)
    setEnabled(now?.enabled ?? true)
    setParams(Object.fromEntries(Object.entries({ ...under, ...(now?.params ?? {}) }).map(([k, v]) => [k, paramText(v)])))
  }, [job, was])

  // another preset picked: the fields left as they were follow it
  const pickPreset = (next: string) => {
    if (!job) return
    const before = baseParams(job, preset || null), after = baseParams(job, next || null)
    setParams((p) => Object.fromEntries(Object.keys({ ...after, ...p }).map((k) => [k, p[k] === undefined || p[k] === paramText(before[k]) ? paramText(after[k]) : p[k]])))
    setPreset(next)
  }

  const jobOptions = useMemo<SearchSelectOption[]>(() => (jobs ?? []).map((j) => ({ value: j.id, label: jobName(j), mono: j.id })), [jobs])
  const scheduleOptions = useMemo<SearchSelectOption[]>(() => [
    ...(job?.schedules ?? []).map((s) => ({ value: s.name, label: s.name })),
    { value: NEW, label: t('nomaflow.scheduleJob.newOne') },
  ], [job, t])
  const presetOptions = useMemo<SearchSelectOption[]>(() => (job?.presets ?? []).map((p) => ({ value: p.name, label: p.name })).sort((a, b) => a.label.localeCompare(b.label)), [job])
  // the environments: the pools of the connectors that run on several databases
  const poolOptions = useMemo<SearchSelectOption[]>(() => {
    const pools = new Set<string>()
    for (const c of connectors ?? []) if (c.type === 'sql' && (c.pools?.length ?? 0) > 1) c.pools!.forEach((p) => pools.add(p))
    return [...pools].sort((a, b) => a.localeCompare(b)).map((p) => ({ value: p, label: p }))
  }, [connectors])
  // the job's shared parameters, each with what the job says of it (a label, a list of choices) when it does
  const declared = useMemo(() => new Map((summaries.find((j) => j.id === jobId)?.user_params ?? []).map((p) => [p.name, p])), [summaries, jobId])
  const under = useMemo(() => (job ? baseParams(job, preset || null) : {}), [job, preset])
  const paramNames = Object.keys(under)

  const values = { name, schedule: schedule ?? '', enabled, preset: preset || null, pool: pool || null, timezone: timezone || null,
    params: Object.fromEntries(paramNames.map((k) => [k, paramValue(params[k] ?? '', under[k])])) }
  const existing = was === NEW || was == null ? null : was
  const problem = jobs ? scheduleProblem(jobs, jobId, existing, values) : null

  const finish = async (next: JobConfig[]) => {
    if (inEditor) { inEditor.onApply(next[0]); onClose(); return }
    setBusy(true); setError(null)
    try {
      await api.put('/admin/config/jobs/parsed', { jobs: next })
      await api.post('/admin/jobs/reload')
      onSaved?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  return (
    <Overlay onClick={onClose}>
      <Modal style={{ width: 'min(640px, 95vw)' }} onClick={(e) => e.stopPropagation()} data-schedule-job>
        <ModalHeader><CalendarClock size={16} style={{ verticalAlign: '-2px', marginRight: 6 }} />{t(existing ? 'nomaflow.scheduleJob.titleChange' : 'nomaflow.scheduleJob.title')}</ModalHeader>
        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          {jobs == null && !error ? <Centered /> : jobs && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {!inEditor && (
                <Field data-schedule-job-pick>
                  <Label>{t('nomaflow.scheduleJob.job')}</Label>
                  <SearchSelect value={jobId} onChange={(v) => { setJobId(v); setWas(NEW) }} options={jobOptions} placeholder={t('nomaflow.scheduleJob.pickJob')} sort />
                </Field>
              )}
              {job && (
                <>
                  {(job.schedules?.length ?? 0) > 0 && !initial?.name && (
                    <Field data-schedule-pick>
                      <Label>{t('nomaflow.scheduleJob.which')}</Label>
                      <SearchSelect value={was ?? NEW} onChange={setWas} options={scheduleOptions} />
                    </Field>
                  )}
                  <Field>
                    <Label>{t('nomaflow.scheduleJob.name')}</Label>
                    <Input data-schedule-name value={name} onChange={(e) => setName(e.target.value)} placeholder={t('nomaflow.scheduleJob.namePlaceholder')} />
                  </Field>
                  <Pair>
                    <Field data-schedule-preset>
                      <Label>{t('nomaflow.scheduleJob.what')}</Label>
                      <SearchSelect value={preset} onChange={pickPreset} options={presetOptions} anyLabel={t('nomaflow.scheduleJob.asItIs')} placeholder={t('nomaflow.scheduleJob.asItIs')} clearable />
                    </Field>
                    {(poolOptions.length > 0 || !!pool) && (
                      <Field data-schedule-pool>
                        <Label>{t('nomaflow.editor.fieldEnvironment')}</Label>
                        <SearchSelect value={pool} onChange={setPool} options={poolOptions} anyLabel={t('nomaflow.scheduleJob.jobsEnvironment')} placeholder={t('nomaflow.scheduleJob.jobsEnvironment')} clearable />
                      </Field>
                    )}
                  </Pair>
                  {paramNames.length > 0 && (
                    <Field data-schedule-params>
                      <Label>{t('nomaflow.scheduleJob.params')}</Label>
                      <ParamGrid>
                        {paramNames.map((key) => {
                          const said = declared.get(key)
                          const choices = !said?.multiple && (said?.choices?.length ?? 0) > 0 ? said!.choices : null
                          return [
                            <Label key={`${key}-l`} title={key}>{said?.label || key}</Label>,
                            choices
                              ? <SearchSelect key={key} value={params[key] ?? ''} onChange={(v) => setParams((p) => ({ ...p, [key]: v }))} options={choices.map((c) => ({ value: c.value, label: c.label }))} allowCustom clearable />
                              : <Input key={key} data-schedule-param={key} value={params[key] ?? ''} placeholder={paramText(under[key])}
                                  onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.value }))} />,
                          ]
                        })}
                      </ParamGrid>
                      <Hint>{t('nomaflow.scheduleJob.paramsHint')}</Hint>
                    </Field>
                  )}
                  <ScheduleField value={schedule} timezone={timezone || job.timezone || null} onChange={setSchedule} />
                  <Pair>
                    <Field>
                      <Label>{t('nomaflow.editor.fieldTimezone')}</Label>
                      <TimezoneSelect value={timezone} onChange={setTimezone} placeholder={job.timezone ?? 'Europe/Paris'} />
                    </Field>
                    <Field style={{ justifyContent: 'flex-end' }}>
                      <Checkbox checked={enabled} onChange={setEnabled} label={t('nomaflow.scheduleJob.active')} />
                    </Field>
                  </Pair>
                </>
              )}
            </div>
          )}
        </ModalBody>
        <ModalFooter>
          {existing && jobs && (
            <Button $variant="ghost" disabled={busy} data-schedule-remove onClick={() => void finish(withoutSchedule(jobs, jobId, existing))} style={{ color: colors.red.main, marginRight: 'auto' }}>
              <Trash2 size={13} /> {t('nomaflow.scheduleJob.remove')}
            </Button>
          )}
          {problem && job && <Hint style={{ flex: 1, textAlign: 'right' }}>{t(problem)}</Hint>}
          <Button $variant="ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button>
          <Button data-schedule-save disabled={busy || !jobs || !!problem} onClick={() => jobs && void finish(withSchedule(jobs, jobId, existing, values))}>
            {t(inEditor ? 'period.apply' : 'common.save')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
