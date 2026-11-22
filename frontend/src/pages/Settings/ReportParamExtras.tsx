// What a report template's parameter has beyond its name and type — the value it starts with, the
// help shown under it, where the choices of its dropdown come from — and what the template itself
// has beyond its text: the formats it is produced in and the PDF settings it overrides.
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Checkbox, Field, Input, SearchSelect, StringMapEditor, Textarea, type SearchSelectOption } from '../../common'
import { freeMapText, mergeFreeMap } from '../../services/freeMap'
import { CHOICE_KINDS, PDF_OPTION_NAMES, choicesList, choicesText, type ChoiceKind, type ParamChoices } from '../../services/reportParams'
import { colors, fontSize, fonts } from '../../theme'

const Grid = styled.div`display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 10px; padding: 4px 0 10px;`
const Hint = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; line-height: 1.4; margin-top: 3px;`

/** The second line of a parameter: default value, help, choices. */
export function ReportParamExtras({ value, otherParams, connectors, onChange }: {
  value: { default: unknown; description: string; options?: ParamChoices | null }
  /** The other parameters of the template: a list may read the connector another one holds. */
  otherParams: string[]
  connectors: string[]
  onChange: (patch: { default?: unknown; description?: string; options?: ParamChoices | null }) => void
}) {
  const { t } = useTranslation()
  const choices = value.options ?? null
  const kind = choices?.kind ?? ''
  const kindOpts: SearchSelectOption[] = CHOICE_KINDS.map((k) => ({ value: k, label: t(`settings.templates.choices.${k}`) }))
  const patchChoices = (p: Partial<ParamChoices>) => onChange({ options: { ...(choices ?? { kind: 'static' }), ...p } as ParamChoices })
  const fromOpts: SearchSelectOption[] = [
    ...otherParams.map((p) => ({ value: `param:${p}`, label: t('settings.templates.choices.fromParam', { name: p }) })),
    ...connectors.map((c) => ({ value: `connector:${c}`, label: c })),
  ]
  const from = choices?.connector_param ? `param:${choices.connector_param}` : choices?.connector ? `connector:${choices.connector}` : ''
  return (
    <Grid data-param-extras>
      <Field label={t('settings.templates.paramDefault')}>
        <Input data-param-default value={value.default == null ? '' : String(value.default)} placeholder={t('settings.templates.paramDefaultNone')}
          onChange={(e) => onChange({ default: e.target.value === '' ? null : e.target.value })} />
      </Field>
      <Field label={t('settings.templates.paramHelp')}>
        <Input data-param-help value={value.description ?? ''} placeholder={t('settings.templates.paramHelpExample')}
          onChange={(e) => onChange({ description: e.target.value })} />
      </Field>
      <Field label={t('settings.templates.choices.label')}>
        <div data-param-choices>
          <SearchSelect value={kind} options={kindOpts} anyLabel={t('settings.templates.choices.none')} placeholder={t('settings.templates.choices.none')}
            onChange={(v) => onChange({ options: v ? { kind: v as ChoiceKind } : null })} />
        </div>
      </Field>
      {kind === 'static' && (
        <div style={{ gridColumn: '1 / -1' }}>
          <Field label={t('settings.templates.choices.values')}>
            <Textarea rows={3} style={{ fontFamily: fonts.mono, minHeight: 0 }} placeholder={'EN = English\nFR = French'}
              defaultValue={choicesText(choices?.values)} onBlur={(e) => patchChoices({ values: choicesList(e.target.value) })} />
          </Field>
          <Hint>{t('settings.templates.choices.valuesHint')}</Hint>
        </div>
      )}
      {(kind === 'schemas' || kind === 'query') && (
        <Field label={t('settings.templates.choices.from')}>
          <SearchSelect sort value={from} options={fromOpts} placeholder={t('common.pick')}
            onChange={(v) => patchChoices(v.startsWith('param:') ? { connector_param: v.slice(6), connector: null } : { connector: v.slice(10) || null, connector_param: null })} />
        </Field>
      )}
      {kind === 'query' && (
        <>
          <Field label={t('settings.templates.choices.queryField')}>
            <Input value={choices?.query ?? ''} placeholder="companies" onChange={(e) => patchChoices({ query: e.target.value })} />
          </Field>
          <Field label={t('settings.templates.choices.valueColumn')}>
            <Input value={choices?.value_column ?? ''} placeholder="COMPANY_CODE" onChange={(e) => patchChoices({ value_column: e.target.value })} />
          </Field>
          <Field label={t('settings.templates.choices.labelColumn')}>
            <Input value={choices?.label_column ?? ''} placeholder="COMPANY_NAME" onChange={(e) => patchChoices({ label_column: e.target.value })} />
          </Field>
        </>
      )}
    </Grid>
  )
}

/** The formats a template is produced in (at least one) and the PDF settings it overrides. */
export function ReportOutput({ formats, pdfOptions, onFormats, onPdfOptions }: {
  formats: ('pdf' | 'markdown')[]
  pdfOptions: Record<string, unknown>
  onFormats: (v: ('pdf' | 'markdown')[]) => void
  onPdfOptions: (v: Record<string, unknown>) => void
}) {
  const { t } = useTranslation()
  const toggle = (f: 'pdf' | 'markdown', on: boolean) => {
    const next = on ? [...new Set([...formats, f])] : formats.filter((x) => x !== f)
    if (next.length) onFormats(next)          // one format at least: the last one cannot be unticked
  }
  return (
    <div data-report-output style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Field label={t('settings.templates.formats')}>
        <div style={{ display: 'flex', gap: 16 }}>
          <Checkbox checked={formats.includes('pdf')} onChange={(v: boolean) => toggle('pdf', v)} label={t('settings.templates.formatPdf')} />
          <Checkbox checked={formats.includes('markdown')} onChange={(v: boolean) => toggle('markdown', v)} label={t('settings.templates.formatText')} />
        </div>
      </Field>
      {formats.includes('pdf') && (
        <div data-report-pdf-options>
          <Field label={t('settings.templates.pdfOptions')}>
            <StringMapEditor
              keyEnum={{ label: t('settings.templates.pdfOptionName'), values: PDF_OPTION_NAMES.map((n) => ({ value: n, label: t(`settings.templates.pdfOption.${n}`) })) }}
              value={freeMapText(pdfOptions)} onChange={(v) => onPdfOptions(mergeFreeMap(pdfOptions, v))} />
          </Field>
          <Hint>{t('settings.templates.pdfOptionsHint')}</Hint>
        </div>
      )}
    </div>
  )
}
