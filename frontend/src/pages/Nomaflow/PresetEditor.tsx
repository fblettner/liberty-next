// One preset of a job, edited where the job is: its name and what it changes in a run — the log, the
// shared values, the steps it runs or skips, a step's own values. When it runs is a schedule's (JobScheduleConfig). A preset was
// created from the Run window only, and could then be neither renamed, changed nor removed.
import { useState } from 'react'
import styled from '@emotion/styled'
import { ChevronDown, ChevronRight, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button, Input, SearchSelect, type SearchSelectOption } from '../../common'
import type { ConnectorMeta } from '../../types/connectors'
import { colors, fontSize, radius } from '../../theme'
import { Field, FieldLabel, Grid, Hint, KeyValueEditor, JOB_PARAM_CATALOG, SubHead } from './stepFields'
import type { JobPreset } from './types'

// the preset folded: one line — its name and what it changes — that opens it
const Fold = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 0; border: none; background: none; cursor: pointer; text-align: left;
  font-family: inherit; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & .n { color: ${colors.text.primary}; font-weight: 600; } & .s { color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const Card = styled.div`
  display: flex; flex-direction: column; gap: 10px;
  padding: 12px; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  background: ${colors.bg.input};
`
const Head = styled.div`display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap;`
const StepLine = styled.div`
  display: grid; grid-template-columns: minmax(0, 1fr) 190px; gap: 8px; align-items: center;
  font-size: ${fontSize.sm}; color: ${colors.text.primary};
`

export const LOG_LEVELS = ['WARNING', 'INFO', 'DEBUG'] as const

export function PresetEditor({ preset, steps, sqlConnectors, onChange, onDelete }: {
  preset: JobPreset
  /** The job's steps, by name: what the preset may run, skip or give values to. */
  steps: string[]
  sqlConnectors: ConnectorMeta[]
  onChange: (p: Partial<JobPreset>) => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const logOpts: SearchSelectOption[] = LOG_LEVELS.map((l) => ({ value: l, label: t(`nomaflow.editor.logLevel.${l}`) }))
  const enabled = preset.step_enabled ?? {}
  const kwargs = preset.op_kwargs ?? {}
  const stepOpts: SearchSelectOption[] = [
    { value: 'run', label: t('nomaflow.editor.presetStepRun') },
    { value: 'skip', label: t('nomaflow.editor.presetStepSkip') },
  ]
  const setStep = (name: string, v: string) => {
    const next = { ...enabled }
    if (v === 'run') next[name] = true
    else if (v === 'skip') next[name] = false
    else delete next[name]
    onChange({ step_enabled: next })
  }
  const setKwargs = (name: string, v: Record<string, unknown>) => {
    const next = { ...kwargs }
    if (Object.keys(v).length) next[name] = v; else delete next[name]
    onChange({ op_kwargs: next })
  }
  // Only the steps the preset says something about are listed (one it names that the job no longer
  // has is still shown, so that it can be removed); another step is added from the list below them.
  const named = [...new Set([...Object.keys(enabled), ...Object.keys(kwargs)])]
  const others: SearchSelectOption[] = steps.filter((s) => !named.includes(s)).map((s) => ({ value: s, label: s }))
  // folded unless it has no name yet (just made, or to fix)
  const [open, setOpen] = useState(() => !preset.name.trim())
  const values = Object.keys(preset.params ?? {}).length
  const runs = Object.values(enabled).filter(Boolean).length, skips = Object.values(enabled).filter((v) => !v).length
  const said = [
    values ? t('nomaflow.editor.presetSum.values', { count: values }) : '',
    runs ? t('nomaflow.editor.presetSum.run', { count: runs }) : '',
    skips ? t('nomaflow.editor.presetSum.skip', { count: skips }) : '',
    Object.keys(kwargs).length ? t('nomaflow.editor.presetSum.stepValues', { count: Object.keys(kwargs).length }) : '',
    preset.log_level ? t(`nomaflow.editor.logLevel.${preset.log_level}`) : '',
  ].filter(Boolean).join(' · ') || t('nomaflow.editor.presetSum.nothing')
  if (!open) return (
    <Card data-preset data-preset-folded>
      <Fold type="button" onClick={() => setOpen(true)}>
        <ChevronRight size={14} /><span className="n">{preset.name}</span><span className="s">{said}</span>
      </Fold>
    </Card>
  )
  return (
    <Card data-preset>
      <Fold type="button" onClick={() => setOpen(false)}><ChevronDown size={14} /><span className="n">{preset.name || t('nomaflow.editor.presetName')}</span></Fold>
      <Head>
        <Field style={{ flex: '1 1 220px' }}>
          <FieldLabel>{t('nomaflow.editor.presetName')}</FieldLabel>
          <Input data-preset-name value={preset.name} onChange={(e) => onChange({ name: e.target.value })} />
        </Field>
        <Button $variant="ghost" $size="sm" onClick={onDelete} style={{ color: colors.red.main }}>
          <Trash2 size={13} /> {t('nomaflow.editor.presetDelete')}
        </Button>
      </Head>
      <Grid>
        <Field $span={2}>
          <FieldLabel>{t('nomaflow.editor.fieldLogLevel')}</FieldLabel>
          <SearchSelect value={preset.log_level ?? ''} onChange={(v) => onChange({ log_level: (v || null) as JobPreset['log_level'] })}
            options={logOpts} anyLabel={t('nomaflow.editor.presetAsJob')} placeholder={t('nomaflow.editor.presetAsJob')} />
        </Field>
      </Grid>
      <div data-preset-params>
        <SubHead>{t('nomaflow.editor.presetValues')}</SubHead>
        <Hint>{t('nomaflow.editor.presetValuesHint')}</Hint>
        <KeyValueEditor value={(preset.params ?? {}) as Record<string, unknown>} onChange={(v) => onChange({ params: v })}
          schema={JOB_PARAM_CATALOG} sqlConnectors={sqlConnectors} />
      </div>
      {(named.length > 0 || others.length > 0) && (
        <div data-preset-steps>
          <SubHead>{t('nomaflow.editor.presetSteps')}</SubHead>
          <Hint>{t('nomaflow.editor.presetStepsHint')}</Hint>
          {named.map((name) => (
            <div key={name} style={{ marginTop: 6 }}>
              <StepLine>
                <span>{name}{!steps.includes(name) && <Hint> — {t('nomaflow.editor.presetStepGone')}</Hint>}</span>
                <SearchSelect value={name in enabled ? (enabled[name] ? 'run' : 'skip') : ''} onChange={(v) => setStep(name, v)}
                  options={stepOpts} anyLabel={t('nomaflow.editor.presetAsJob')} placeholder={t('nomaflow.editor.presetAsJob')} />
              </StepLine>
              {name in kwargs && (
                <KeyValueEditor value={kwargs[name] ?? {}} onChange={(v) => setKwargs(name, v)}
                  heads={[t('nomaflow.editor.presetStepValue', { step: name }), t('nomaflow.steps.reportParamValue')]} />
              )}
            </div>
          ))}
          {others.length > 0 && (
            <div style={{ marginTop: 8, maxWidth: 320 }} data-preset-add-step>
              <SearchSelect sort value="" onChange={(v) => { if (v) setStep(v, 'run') }} options={others} placeholder={t('nomaflow.editor.presetAddStep')} />
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
