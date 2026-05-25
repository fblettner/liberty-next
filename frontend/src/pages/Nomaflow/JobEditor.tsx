// nomaflow Job editor (NOMAFLOW-UI.md §3.2). Increment 3: the job-level panel —
// id / description / schedule / timezone / enabled / tags / retry / alerts — plus the
// step list shown read-only. Increment 4 makes the steps editable.
//
// The whole jobs.toml is rewritten on save (PUT /admin/config/jobs/parsed replaces the
// [[jobs]] array), so the editor loads the full job list, edits one entry, and writes
// the merged list back — then POST /admin/reload to apply.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Save, Workflow, Layers } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  PageLayout, Button, Banner, Centered, Card, Input, Select, Checkbox,
  SpinnerRing, Stack,
} from '../../common'
import { colors, fontSize } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import StepEditor from './StepEditor'
import ScheduleField from './ScheduleField'
import type { JobConfig, JobsParsedResponse } from './types'

const Section = styled(Card)`display: flex; flex-direction: column; gap: 12px;`
const SectionTitle = styled.div`
  display: flex; align-items: center; gap: 7px;
  font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.md};
`
const Grid = styled.div`
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px;
`
const FieldWrap = styled.label<{ $full?: boolean }>`
  display: flex; flex-direction: column; gap: 4px;
  grid-column: ${({ $full }) => ($full ? '1 / -1' : 'auto')};
`
const FieldLabel = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const FieldHint = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const Toolbar = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 14px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1;`
const StatusText = styled.span<{ $tone: 'muted' | 'ok' }>`
  font-size: ${fontSize.sm};
  color: ${({ $tone }) => ($tone === 'ok' ? colors.green.main : colors.text.muted)};
`

const blankJob = (): JobConfig => ({ id: '', description: '', schedule: '', enabled: true, tags: [], steps: [] })

export default function JobEditor() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { connectors } = useWorkspace()      // for the step forms' connector dropdowns
  const { id } = useParams()                 // undefined on /nomaflow/jobs/new
  const isNew = id === undefined

  const [allJobs, setAllJobs] = useState<JobConfig[] | null>(null)
  const [job, setJob] = useState<JobConfig | null>(null)
  const [original, setOriginal] = useState('')        // JSON snapshot for the dirty check
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setError(null)
    api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
      .then((r) => {
        setAllJobs(r.jobs)
        const found = isNew ? blankJob() : r.jobs.find((j) => j.id === id)
        if (!found) { setError(t('nomaflow.editor.notFound', { id })); return }
        const copy: JobConfig = JSON.parse(JSON.stringify(found))
        setJob(copy)
        setOriginal(JSON.stringify(copy))
      })
      .catch((e) => setError(e instanceof ApiError
        ? (e.status === 403 ? t('nomaflow.superuserRequired') : e.message)
        : String(e)))
  }, [id, isNew, t])

  const dirty = useMemo(() => job != null && JSON.stringify(job) !== original, [job, original])
  const patch = useCallback((p: Partial<JobConfig>) => setJob((j) => (j ? { ...j, ...p } : j)), [])

  const save = useCallback(async () => {
    if (!job || !allJobs) return
    const jid = job.id.trim()
    if (!jid) { setError(t('nomaflow.editor.idRequired')); return }
    setBusy(true); setError(null); setStatus(null)
    try {
      // Sanitise list-of-strings fields: drop empty/whitespace-only rows the operator may have
      // added but not filled in (StringListEditor keeps the blank row alive so they can type
      // into it — the cleanup only happens here, at save). Strip the field entirely when the
      // list is empty so the TOML stays free of empty arrays.
      const cleaned = job.steps.map((s) => {
        const sbc = (s as Record<string, unknown>).strip_both_columns
        if (!Array.isArray(sbc)) return s
        const kept = (sbc as unknown[]).map((x) => String(x ?? '').trim()).filter(Boolean)
        const next = { ...s } as Record<string, unknown>
        if (kept.length === 0) delete next.strip_both_columns
        else next.strip_both_columns = kept
        return next as typeof s
      })
      const cleanedJob = { ...job, steps: cleaned }
      // Merge the working copy into the full list: replace in place when editing,
      // append when new. `id` is immutable in edit mode (the field is read-only).
      const merged = isNew
        ? [...allJobs, cleanedJob]
        : allJobs.map((j) => (j.id === id ? cleanedJob : j))
      await api.put('/admin/config/jobs/parsed', { jobs: merged })
      await api.post('/admin/reload')
      navigate('/nomaflow')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      setBusy(false)
    }
  }, [job, allJobs, id, isNew, navigate, t])

  return (
    <PageLayout
      icon={<Workflow size={18} />}
      title={isNew ? t('nomaflow.editor.newTitle') : t('nomaflow.editor.editTitle', { id })}
      description={t('nomaflow.editor.subtitle')}
    >
      <Toolbar>
        <Button $variant="ghost" $size="sm" onClick={() => navigate('/nomaflow')} disabled={busy}>
          <ArrowLeft size={14} /> {t('nomaflow.editor.backToJobs')}
        </Button>
        <Spacer />
        {dirty && <StatusText $tone="muted">{t('nomaflow.editor.unsaved')}</StatusText>}
        {status && <StatusText $tone="ok">{status}</StatusText>}
        <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !job || !dirty}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
        </Button>
      </Toolbar>

      {error && <Banner $tone="error" style={{ marginBottom: 12 }}>{error}</Banner>}
      {!job && !error && <Centered />}

      {job && (
        <Stack gap={14}>
          {/* ── job-level settings ─────────────────────────────────────────── */}
          <Section>
            <SectionTitle><Workflow size={15} /> {t('nomaflow.editor.jobSection')}</SectionTitle>
            <Grid>
              <FieldWrap>
                <FieldLabel>{t('nomaflow.editor.fieldId')}</FieldLabel>
                <Input
                  value={job.id}
                  readOnly={!isNew}
                  onChange={(e) => patch({ id: e.target.value })}
                  placeholder="nomajde-daily-sync"
                />
                {!isNew && <FieldHint>{t('nomaflow.editor.idImmutable')}</FieldHint>}
              </FieldWrap>
              <FieldWrap $full>
                <FieldLabel>{t('nomaflow.editor.fieldDescription')}</FieldLabel>
                <Input
                  value={job.description ?? ''}
                  onChange={(e) => patch({ description: e.target.value || null })}
                />
              </FieldWrap>
              <FieldWrap $full as="div">
                <ScheduleField
                  value={job.schedule ?? null}
                  timezone={job.timezone ?? null}
                  onChange={(schedule) => patch({ schedule })}
                />
              </FieldWrap>
              <FieldWrap>
                <FieldLabel>{t('nomaflow.editor.fieldTimezone')}</FieldLabel>
                <Input
                  value={job.timezone ?? ''}
                  onChange={(e) => patch({ timezone: e.target.value || null })}
                  placeholder="Europe/Paris"
                />
              </FieldWrap>
              <FieldWrap>
                <FieldLabel>{t('nomaflow.editor.fieldTags')}</FieldLabel>
                <Input
                  value={(job.tags ?? []).join(', ')}
                  onChange={(e) => patch({
                    tags: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                  })}
                  placeholder="nomajde, etl"
                />
              </FieldWrap>
              <FieldWrap>
                <Checkbox
                  checked={job.enabled ?? true}
                  onChange={(checked) => patch({ enabled: checked })}
                  label={t('nomaflow.editor.fieldEnabled')}
                />
              </FieldWrap>
            </Grid>
          </Section>

          {/* ── retry policy ───────────────────────────────────────────────── */}
          <Section>
            <SectionTitle>{t('nomaflow.editor.retrySection')}</SectionTitle>
            <Checkbox
              checked={job.retry != null}
              onChange={(checked) => patch({
                retry: checked ? { attempts: 1, backoff: 'fixed', base_seconds: 60 } : null,
              })}
              label={t('nomaflow.editor.retryEnable')}
            />
            {job.retry && (
              <Grid>
                <FieldWrap>
                  <FieldLabel>{t('nomaflow.editor.retryAttempts')}</FieldLabel>
                  <Input
                    type="number" min={1}
                    value={job.retry.attempts}
                    onChange={(e) => patch({ retry: { ...job.retry!, attempts: Number(e.target.value) || 1 } })}
                  />
                </FieldWrap>
                <FieldWrap>
                  <FieldLabel>{t('nomaflow.editor.retryBackoff')}</FieldLabel>
                  <Select
                    value={job.retry.backoff}
                    onChange={(e) => patch({ retry: { ...job.retry!, backoff: e.target.value as 'fixed' | 'exponential' } })}
                  >
                    <option value="fixed">fixed</option>
                    <option value="exponential">exponential</option>
                  </Select>
                </FieldWrap>
                <FieldWrap>
                  <FieldLabel>{t('nomaflow.editor.retryBaseSeconds')}</FieldLabel>
                  <Input
                    type="number" min={0}
                    value={job.retry.base_seconds}
                    onChange={(e) => patch({ retry: { ...job.retry!, base_seconds: Number(e.target.value) || 0 } })}
                  />
                </FieldWrap>
              </Grid>
            )}
          </Section>

          {/* ── alerts ─────────────────────────────────────────────────────── */}
          <Section>
            <SectionTitle>{t('nomaflow.editor.alertsSection')}</SectionTitle>
            <Checkbox
              checked={job.alerts != null}
              onChange={(checked) => patch({
                alerts: checked
                  ? { on_failure: true, on_long_run_minutes: null, recipients: [] }
                  : null,
              })}
              label={t('nomaflow.editor.alertsEnable')}
            />
            {job.alerts && (
              <Grid>
                <FieldWrap>
                  <Checkbox
                    checked={job.alerts.on_failure}
                    onChange={(checked) => patch({ alerts: { ...job.alerts!, on_failure: checked } })}
                    label={t('nomaflow.editor.alertsOnFailure')}
                  />
                </FieldWrap>
                <FieldWrap>
                  <FieldLabel>{t('nomaflow.editor.alertsLongRun')}</FieldLabel>
                  <Input
                    type="number" min={1}
                    value={job.alerts.on_long_run_minutes ?? ''}
                    onChange={(e) => patch({
                      alerts: { ...job.alerts!, on_long_run_minutes: e.target.value ? Number(e.target.value) : null },
                    })}
                    placeholder={t('nomaflow.editor.alertsLongRunPlaceholder')}
                  />
                </FieldWrap>
                <FieldWrap $full>
                  <FieldLabel>{t('nomaflow.editor.alertsRecipients')}</FieldLabel>
                  <Input
                    value={(job.alerts.recipients ?? []).join(', ')}
                    onChange={(e) => patch({
                      alerts: {
                        ...job.alerts!,
                        recipients: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                      },
                    })}
                    placeholder="admin"
                  />
                </FieldWrap>
              </Grid>
            )}
          </Section>

          {/* ── step pipeline ──────────────────────────────────────────────── */}
          <Section>
            <SectionTitle><Layers size={15} /> {t('nomaflow.editor.stepsSection', { count: job.steps.length })}</SectionTitle>
            <StepEditor
              steps={job.steps}
              onChange={(steps) => patch({ steps })}
              connectors={connectors}
            />
          </Section>
        </Stack>
      )}
    </PageLayout>
  )
}
