// nomaflow Job editor (NOMAFLOW-UI.md §3.2). Increment 3: the job-level panel —
// id / description / schedule / timezone / enabled / tags / retry / alerts — plus the
// step list shown read-only. Increment 4 makes the steps editable.
//
// The whole jobs.toml is rewritten on save (PUT /admin/config/jobs/parsed replaces the
// [[jobs]] array), so the editor loads the full job list, edits one entry, and writes
// the merged list back — then POST /admin/reload to apply.
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate, useParams } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Save, Workflow, Layers, X, Plus, Check } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  PageLayout, Button, Banner, Centered, Card, Input, Select, Checkbox,
  SpinnerRing, Stack,
} from '../../common'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import StepEditor, { KeyValueEditor, JOB_PARAM_CATALOG } from './StepEditor'
import ScheduleField from './ScheduleField'
import type { JobConfig, JobPreset, JobsParsedResponse } from './types'

const Section = styled(Card)`display: flex; flex-direction: column; gap: 12px;`
const PresetCard = styled.div`
  display: flex; flex-direction: column; gap: 10px;
  padding: 12px; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  background: ${colors.bg.input};
`
const PresetHead = styled.div`display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;`
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

// ── chip-based tag editor ────────────────────────────────────────────────────────
// Comma-separated text input was a usability problem: operators typo'd tags
// (``securty`` instead of ``security`` → ghost tag in the JobsList filter), and
// duplicating jobs accumulated tag noise nobody could clean up without TOML edits.
// This editor lists current tags as removable chips + autocompletes new entries
// from every tag already in use across jobs.toml, so the operator picks an
// existing one (no typo) or types a new one knowingly.
const TagsBox = styled.div`
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  min-height: 32px; padding: 4px 6px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  background: ${colors.bg.input};
  position: relative;
`
const TagPill = styled.span`
  display: inline-flex; align-items: center; gap: 4px;
  height: 22px; padding: 0 4px 0 8px;
  border: 1px solid ${colors.blue.border}; border-radius: ${radius.sm};
  background: ${colors.blue.bg}; color: ${colors.blue.main};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
`
const TagPillX = styled.button`
  background: transparent; border: none; padding: 2px; cursor: pointer;
  display: inline-flex; align-items: center; color: ${colors.blue.main};
  &:hover { color: ${colors.text.primary}; }
`
const TagInputBare = styled.input`
  flex: 1; min-width: 100px; border: none; background: transparent; outline: none;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &::placeholder { color: ${colors.text.muted}; }
`
// Portal-rendered to ``document.body`` with ``position: fixed`` so it escapes the
// JobEditor's ``<Card>`` stacking context (otherwise the next form Section painted
// over it). Chrome matches SearchSelect's Panel — same radius, shadow, scroll
// behaviour — so the dropdown reads as the same control type the operator sees
// everywhere else in Liberty (the standard Pick-A-Thing dropdown).
const SuggestionList = styled.div`
  position: fixed; z-index: 1000;
  display: flex; flex-direction: column;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border};
  border-radius: ${radius.lg}; box-shadow: ${shadow.lg};
  overflow: hidden;
  max-height: 280px;
`
const SuggestionScroll = styled.div`
  flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 4px 0;
`
// Active state = the keyboard-highlighted row (arrow keys); the hover state is
// distinct and uses the framework's hover-subtle var (matches SearchSelect Item).
const SuggestionItem = styled.button<{ $highlighted?: boolean }>`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 12px;
  border: none; text-align: left; cursor: pointer;
  background: ${({ $highlighted }) => ($highlighted ? colors.blue.bg : 'transparent')};
  color: ${({ $highlighted }) => ($highlighted ? colors.blue.main : colors.text.secondary)};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  & .check { color: ${colors.blue.main}; flex-shrink: 0; opacity: 0; }
  &[data-highlighted="true"] .check { opacity: 1; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
// "Create new tag" row pinned to the bottom of the dropdown when the typed value
// doesn't match any existing tag. Same shape as SearchSelect.CreateRow — makes
// the "I'm adding a NEW tag" path discoverable instead of operators having to
// guess that Enter commits a typed value.
const SuggestionCreateRow = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 12px;
  border: none; border-top: 1px solid ${colors.border};
  background: transparent; cursor: pointer; text-align: left;
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & .mono { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const SuggestionEmpty = styled.div`
  padding: 10px 12px; color: ${colors.text.muted};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
`

function TagsField(
  { value, allTags, onChange, placeholder }: {
    value: string[]
    allTags: string[]
    onChange: (next: string[]) => void
    placeholder?: string
  },
) {
  const { t } = useTranslation()
  const [typed, setTyped] = useState('')
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  // Anchor + computed coords for the portaled SuggestionList. ``boxRef`` is on the
  // TagsBox (the visible trigger surface); the panel renders at ``position: fixed``
  // anchored under it, recomputed on scroll/resize so it follows the trigger as
  // the operator scrolls the long JobEditor form.
  const boxRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [panelPos, setPanelPos] = useState<{ top: number; left: number; width: number } | null>(null)
  useEffect(() => {
    if (!open) { setPanelPos(null); return }
    const compute = () => {
      const r = boxRef.current?.getBoundingClientRect()
      if (!r) return
      // Open below the box with a 4px gap. The box-shadow + max-height (220px in
      // the styled component) keep it visible even near the viewport edge.
      setPanelPos({ top: r.bottom + 4, left: r.left, width: r.width })
    }
    compute()
    window.addEventListener('resize', compute)
    // capture:true catches scroll on every ancestor (the JobEditor form is inside
    // a scrolling container) — bubbling scroll skips non-Window scrollers.
    window.addEventListener('scroll', compute, true)
    return () => {
      window.removeEventListener('resize', compute)
      window.removeEventListener('scroll', compute, true)
    }
  }, [open])

  // Suggestions = all tags NOT already on this job, filtered by what the operator
  // is typing (case-insensitive substring). Sorted alphabetically. Empty input +
  // empty allTags = no dropdown shown.
  const suggestions = useMemo(() => {
    const used = new Set(value.map((t) => t.toLowerCase()))
    const needle = typed.trim().toLowerCase()
    return allTags
      .filter((tg) => !used.has(tg.toLowerCase()))
      .filter((tg) => !needle || tg.toLowerCase().includes(needle))
      .sort((a, b) => a.localeCompare(b))
  }, [value, allTags, typed])

  const addTag = (raw: string) => {
    const tg = raw.trim()
    if (!tg) return
    if (value.some((t) => t.toLowerCase() === tg.toLowerCase())) return  // de-dup
    onChange([...value, tg])
    setTyped('')
    setHighlight(0)
  }
  const removeTag = (tg: string) => onChange(value.filter((t) => t !== tg))
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      // Picking a suggestion while the dropdown is open beats committing what's
      // typed — operator likely arrowed-down to a match.
      if (open && suggestions[highlight]) addTag(suggestions[highlight])
      else addTag(typed)
    } else if (e.key === 'Backspace' && typed === '' && value.length > 0) {
      // Empty input + Backspace = pop the last chip (familiar tag-editor pattern).
      removeTag(value[value.length - 1])
    } else if (e.key === 'ArrowDown' && open) {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp' && open) {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <TagsBox
      ref={boxRef}
      onClick={(e) => {
        // Click anywhere in the box (not on a chip's X) focuses the input — feels
        // like one continuous field, matching macOS Mail / GitHub label inputs.
        if (e.target === e.currentTarget) inputRef.current?.focus()
      }}
    >
      {value.map((tg) => (
        <TagPill key={tg}>
          {tg}
          <TagPillX type="button" onClick={() => removeTag(tg)} aria-label={`Remove ${tg}`}>
            <X size={11} />
          </TagPillX>
        </TagPill>
      ))}
      <TagInputBare
        ref={inputRef}
        value={typed}
        onChange={(e) => { setTyped(e.target.value); setOpen(true); setHighlight(0) }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          // Slight delay so a click on a suggestion fires before we hide the list.
          setTimeout(() => setOpen(false), 120)
        }}
        onKeyDown={onKeyDown}
        placeholder={value.length === 0 ? placeholder : ''}
      />
      {open && panelPos && (() => {
        // Build the panel content: suggestions (if any), then a "create new tag"
        // row when the typed value isn't already in the suggestions or the
        // already-applied tags. Don't show the panel at all when there's nothing
        // useful to display (no suggestions + no typed value).
        const typedTrimmed = typed.trim()
        const alreadyApplied = value.some((t) => t.toLowerCase() === typedTrimmed.toLowerCase())
        const exactMatch = suggestions.some((tg) => tg.toLowerCase() === typedTrimmed.toLowerCase())
        const showCreate = !!typedTrimmed && !alreadyApplied && !exactMatch
        if (suggestions.length === 0 && !showCreate && !typedTrimmed) return null
        return createPortal(
          <SuggestionList
            style={{ top: panelPos.top, left: panelPos.left, minWidth: panelPos.width }}
          >
            <SuggestionScroll>
              {suggestions.length > 0 ? (
                suggestions.map((tg, i) => (
                  <SuggestionItem
                    key={tg}
                    type="button"
                    $highlighted={i === highlight}
                    data-highlighted={i === highlight}
                    onMouseDown={(e) => { e.preventDefault(); addTag(tg) }}
                    onMouseEnter={() => setHighlight(i)}
                  >
                    <Check size={12} className="check" />
                    <span>{tg}</span>
                  </SuggestionItem>
                ))
              ) : !showCreate ? (
                <SuggestionEmpty>{t('nomaflow.editor.tagsNoMatch', 'No matching tags.')}</SuggestionEmpty>
              ) : null}
            </SuggestionScroll>
            {showCreate && (
              <SuggestionCreateRow
                type="button"
                onMouseDown={(e) => { e.preventDefault(); addTag(typedTrimmed) }}
              >
                <Plus size={13} />
                {t('nomaflow.editor.tagsCreate', 'Add')}
                <span className="mono">{typedTrimmed}</span>
              </SuggestionCreateRow>
            )}
          </SuggestionList>,
          document.body,
        )
      })()}
    </TagsBox>
  )
}

const blankJob = (): JobConfig => ({ id: '', description: '', schedule: '', enabled: true, tags: [], steps: [] })

/** One-line summary of what a preset overrides — shown next to its name in the editor. */
function presetOverrideSummary(p: JobPreset): string {
  const bits: string[] = []
  if (p.log_level) bits.push(p.log_level)
  const np = Object.keys(p.params ?? {}).length
  if (np) bits.push(`${np} ${np > 1 ? 'params' : 'param'}`)
  const nk = Object.keys(p.op_kwargs ?? {}).length
  if (nk) bits.push(`${nk} step-kwargs`)
  const ne = Object.keys(p.step_enabled ?? {}).length
  if (ne) bits.push(`${ne} step ${ne > 1 ? 'toggles' : 'toggle'}`)
  return bits.length ? bits.join(' · ') : 'no overrides'
}

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
  // Patch one preset in place (schedule / timezone editing). Presets themselves are
  // created from the Run-with-parameters modal (Save preset); here we only attach a cron.
  const patchPreset = useCallback((idx: number, p: Partial<JobPreset>) => setJob((j) => {
    if (!j?.presets) return j
    return { ...j, presets: j.presets.map((pr, i) => (i === idx ? { ...pr, ...p } : pr)) }
  }), [])

  // Tag catalog — every distinct tag in use across jobs.toml, drives the
  // autocomplete in the TagsField below. Recomputed when allJobs changes
  // (after a save / external edit).
  const allTagsCatalog = useMemo<string[]>(() => {
    if (!allJobs) return []
    const acc = new Set<string>()
    for (const j of allJobs) for (const tg of (j.tags ?? [])) acc.add(tg)
    return [...acc].sort((a, b) => a.localeCompare(b))
  }, [allJobs])

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
                <TagsField
                  value={job.tags ?? []}
                  allTags={allTagsCatalog}
                  onChange={(next) => patch({ tags: next })}
                  placeholder={t('nomaflow.editor.tagsPlaceholder', 'Type to add a tag, or pick from existing…')}
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

          {/* ── presets & schedules ─────────────────────────────────────────── */}
          <Section>
            <SectionTitle><Layers size={15} /> {t('nomaflow.editor.presetsSection', 'Presets & schedules')}</SectionTitle>
            {(job.presets?.length ?? 0) === 0 ? (
              <FieldHint>
                {t('nomaflow.editor.presetsEmpty', 'No presets yet. Save one from a job’s Run → Save preset, then give it a schedule here to fire this job on a cron with those parameters — no cloning.')}
              </FieldHint>
            ) : (
              <>
                <FieldHint>
                  {t('nomaflow.editor.presetsHint', 'Give a preset its own cron to run this job on that schedule with the preset’s parameters. Leave the schedule empty to keep it manual-only (Run → pick preset).')}
                </FieldHint>
                {(job.presets ?? []).map((p, i) => (
                  <PresetCard key={p.name}>
                    <PresetHead>
                      <strong>{p.name}</strong>
                      <FieldHint>{presetOverrideSummary(p)}</FieldHint>
                    </PresetHead>
                    <Grid>
                      <FieldWrap $full as="div">
                        <ScheduleField
                          value={p.schedule ?? null}
                          timezone={p.timezone ?? job.timezone ?? null}
                          onChange={(schedule) => patchPreset(i, { schedule })}
                        />
                      </FieldWrap>
                      <FieldWrap>
                        <FieldLabel>{t('nomaflow.editor.fieldTimezone')}</FieldLabel>
                        <Input
                          value={p.timezone ?? ''}
                          onChange={(e) => patchPreset(i, { timezone: e.target.value || null })}
                          placeholder={job.timezone ?? 'Europe/Paris'}
                        />
                      </FieldWrap>
                    </Grid>
                  </PresetCard>
                ))}
              </>
            )}
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

          {/* ── job-level params ────────────────────────────────────────────
              Kwargs merged UNDER every python step's op_kwargs (step wins on
              conflict). The "set once, inherit everywhere" knob — used by agent
              jobs (nomasx1-security, nomasx1-database, ...) that fire N module
              callables all sharing apps_id / source_connector / target_connector. */}
          <Section>
            <SectionTitle>{t('nomaflow.editor.paramsSection', 'Shared params')}</SectionTitle>
            <div style={{ color: colors.text.muted, fontSize: fontSize.sm, marginBottom: 4 }}>
              {t(
                'nomaflow.editor.paramsHint',
                'Merged under every python step\'s op_kwargs (step wins on conflict). Use for values shared across all steps — apps_id, source_connector, target_connector, etc.',
              )}
            </div>
            <KeyValueEditor
              value={(job.params ?? {}) as Record<string, unknown>}
              onChange={(v) => patch({ params: v })}
              // Catalog of well-known python-step kwargs (apps_id / source_connector /
              // target_connector / source_schema / target_schema). Picking one opens the
              // matching widget (connector dropdown / schema dropdown that follows a sibling /
              // text). Custom keys stay available via the picker's "Custom key…" row.
              schema={JOB_PARAM_CATALOG}
              sqlConnectors={(connectors ?? []).filter((c) => c.type === 'sql')}
            />
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
