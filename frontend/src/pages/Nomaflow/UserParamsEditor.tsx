// What a user may set when starting the job from its menu page (`user_params`): one card per
// parameter — its name, the label the page shows (per language), the step that receives it, where
// its choices come from, its default. Until now these were written by hand in jobs.toml.
//
// The choices' source is one string in the file; services/jobParams.ts reads it into the fields
// shown here and writes it back (a source it does not know is kept as written).
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Plus, Trash2 } from 'lucide-react'
import { Button, Checkbox, Input, LocalizedTexts, SearchSelect, StringListEditor, type SearchSelectOption } from '../../common'
import type { ConnectorMeta } from '../../types/connectors'
import type { Localized } from '../../services/localized'
import { composeChoicesFrom, parseChoicesFrom, type ChoiceSource, type UserParamConfig } from '../../services/jobParams'
import { colors, fontSize, fonts, radius } from '../../theme'

const Box = styled.div`
  display: flex; flex-direction: column; gap: 10px; padding: 12px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input};
`
const Row = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; align-items: end;`
const Cell = styled.label<{ $full?: boolean }>`display: flex; flex-direction: column; gap: 4px; min-width: 0; grid-column: ${(p) => (p.$full ? '1 / -1' : 'auto')};`
const Label = styled.span`font-size: ${fontSize.micro}; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: ${colors.text.muted};`
const Hint = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Head = styled.div`display: flex; align-items: center; gap: 8px; & .name { font-family: ${fonts.mono}; color: ${colors.text.primary}; font-size: ${fontSize.sm}; } & .spacer { flex: 1; }`

type Kind = ChoiceSource['kind'] | 'fixed'

export function UserParamsEditor({ value, onChange, steps, connectors }: {
  value: UserParamConfig[]
  onChange: (next: UserParamConfig[]) => void
  /** the job's step names: a parameter may go to one step only */
  steps: string[]
  connectors: ConnectorMeta[]
}) {
  const { t } = useTranslation()
  const set = (i: number, patch: Partial<UserParamConfig>) => onChange(value.map((p, j) => (j === i ? { ...p, ...patch } : p)))
  const sql = connectors.filter((c) => c.type === 'sql')
  const connectorOpts: SearchSelectOption[] = sql.map((c) => ({ value: c.name, label: c.name, mono: c.name }))
  const everyConnector: SearchSelectOption[] = connectors.map((c) => ({ value: c.name, label: c.name, mono: c.name }))
  const queryOpts = (connector: string): SearchSelectOption[] =>
    (sql.find((c) => c.name === connector)?.queries ?? []).map((q) => ({ value: q.name, label: q.description || q.label || q.name, mono: q.name }))
  const kinds: { value: Kind; label: string }[] = [
    { value: 'none', label: t('nomaflow.editor.userParams.kindNone') },
    { value: 'fixed', label: t('nomaflow.editor.userParams.kindFixed') },
    { value: 'query', label: t('nomaflow.editor.userParams.kindQuery') },
    { value: 'explorer_models', label: t('nomaflow.editor.userParams.kindModels') },
    { value: 'test_suites', label: t('nomaflow.editor.userParams.kindSuites') },
    { value: 'pools', label: t('nomaflow.editor.userParams.kindPools') },
    { value: 'connectors', label: t('nomaflow.editor.userParams.kindConnectors') },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {value.map((p, i) => {
        const src = parseChoicesFrom(p.choices_from)
        const fixed = (p.choices?.length ?? 0) > 0
        // one kind shown: where the choices are read, else the fixed list, else a value typed freely
        const kind: Kind = src.kind !== 'none' ? src.kind : fixed ? 'fixed' : 'none'
        const from = (next: ChoiceSource) => set(i, { choices_from: composeChoicesFrom(next) || null, ...(next.kind !== 'none' ? { choices: [] } : {}) })
        const pickKind = (k: string) => {
          if (k === 'fixed') return set(i, { choices_from: null, choices: p.choices?.length ? p.choices : [''] })
          if (k === 'query') return from({ kind: 'query', connector: '', query: '', value: '', label: '' })
          if (k === 'explorer_models') return from({ kind: 'explorer_models', app: '' })
          if (k === 'test_suites') return from({ kind: 'test_suites', connector: '' })
          if (k === 'pools') return from({ kind: 'pools', connector: '' })
          if (k === 'connectors') return from({ kind: 'connectors', type: 'sql' })
          return set(i, { choices_from: null, choices: [] })
        }
        return (
          <Box key={i} data-user-param={i}>
            <Head>
              <span className="name">{p.name || t('nomaflow.editor.userParams.unnamed')}</span>
              <span className="spacer" />
              <Button $variant="ghost" $size="sm" onClick={() => onChange(value.filter((_, j) => j !== i))} style={{ color: colors.red.main }} title={t('common.remove')}><Trash2 size={13} /></Button>
            </Head>
            <Row>
              <Cell>
                <Label>{t('nomaflow.editor.userParams.name')}</Label>
                <Input value={p.name} onChange={(e) => set(i, { name: e.target.value })} placeholder="apps_id" style={{ fontFamily: fonts.mono }} />
              </Cell>
              <Cell>
                <Label>{t('nomaflow.editor.userParams.label')}</Label>
                <Input value={p.label ?? ''} onChange={(e) => set(i, { label: e.target.value })} placeholder={p.name} />
              </Cell>
              <Cell as="div">
                <Label>{t('nomaflow.editor.userParams.step')}</Label>
                <SearchSelect value={p.step ?? ''} onChange={(v) => set(i, { step: v || null })} anyLabel={t('nomaflow.editor.userParams.everyStep')} placeholder={t('nomaflow.editor.userParams.everyStep')}
                  options={steps.map((s) => ({ value: s, label: s, mono: s }))} allowCustom />
              </Cell>
            </Row>
            <LocalizedTexts
              value={Object.fromEntries(Object.entries(p.l ?? {}).map(([lang, v]) => [lang, { label: v }])) as Localized}
              onChange={(l) => set(i, { l: Object.fromEntries(Object.entries(l ?? {}).map(([lang, texts]) => [lang, String(texts.label ?? '')]).filter(([, v]) => v)) })}
              fields={[{ key: 'label', label: t('nomaflow.editor.userParams.label'), base: p.label || p.name }]} />
            <Row>
              <Cell as="div">
                <Label>{t('nomaflow.editor.userParams.choices')}</Label>
                {kind === 'other'
                  ? <Input value={src.kind === 'other' ? src.text : ''} onChange={(e) => set(i, { choices_from: e.target.value })} style={{ fontFamily: fonts.mono }} />
                  : <SearchSelect value={kind} onChange={pickKind} options={kinds} />}
              </Cell>
              {src.kind === 'query' && (
                <>
                  <Cell as="div">
                    <Label>{t('nomaflow.editor.userParams.connector')}</Label>
                    <SearchSelect sort value={src.connector} onChange={(v) => from({ ...src, connector: v, query: '' })} options={connectorOpts} allowCustom placeholder={t('common.pick')} />
                  </Cell>
                  <Cell as="div">
                    <Label>{t('nomaflow.editor.userParams.query')}</Label>
                    <SearchSelect sort value={src.query} onChange={(v) => from({ ...src, query: v })} options={queryOpts(src.connector)} allowCustom placeholder={t('common.pick')} />
                  </Cell>
                  <Cell>
                    <Label>{t('nomaflow.editor.userParams.valueColumn')}</Label>
                    <Input value={src.value} onChange={(e) => from({ ...src, value: e.target.value })} placeholder="APPS_ID" style={{ fontFamily: fonts.mono }} />
                  </Cell>
                  <Cell>
                    <Label>{t('nomaflow.editor.userParams.labelColumn')}</Label>
                    <Input value={src.label} onChange={(e) => from({ ...src, label: e.target.value })} placeholder="APPS_NAME" style={{ fontFamily: fonts.mono }} />
                  </Cell>
                </>
              )}
              {src.kind === 'explorer_models' && (
                <Cell as="div">
                  <Label>{t('nomaflow.editor.userParams.app')}</Label>
                  <SearchSelect sort value={src.app} onChange={(v) => from({ kind: 'explorer_models', app: v })} options={everyConnector} allowCustom placeholder={t('common.pick')} />
                </Cell>
              )}
              {src.kind === 'pools' && (
                <Cell as="div">
                  <Label>{t('nomaflow.editor.userParams.poolsOf')}</Label>
                  <SearchSelect sort value={src.connector} onChange={(v) => from({ kind: 'pools', connector: v })} options={everyConnector} allowCustom anyLabel={t('nomaflow.editor.userParams.everyPool')} placeholder={t('nomaflow.editor.userParams.everyPool')} />
                </Cell>
              )}
              {src.kind === 'connectors' && (
                <Cell as="div">
                  <Label>{t('nomaflow.editor.userParams.connectorsType')}</Label>
                  <SearchSelect value={src.type} onChange={(v) => from({ kind: 'connectors', type: (v as '' | 'sql' | 'api') })}
                    options={[{ value: 'sql', label: t('nomaflow.editor.userParams.connectorsSql') }, { value: 'api', label: t('nomaflow.editor.userParams.connectorsApi') }]}
                    anyLabel={t('nomaflow.editor.userParams.everyConnector')} placeholder={t('nomaflow.editor.userParams.everyConnector')} />
                </Cell>
              )}
              {src.kind === 'test_suites' && (
                <Cell as="div">
                  <Label>{t('nomaflow.editor.userParams.suitesOf')}</Label>
                  <SearchSelect sort value={src.connector} onChange={(v) => from({ kind: 'test_suites', connector: v })} options={everyConnector} allowCustom anyLabel={t('nomaflow.editor.userParams.everySuite')} placeholder={t('nomaflow.editor.userParams.everySuite')} />
                </Cell>
              )}
            </Row>
            {kind === 'fixed' && (
              <Cell as="div" $full>
                <Label>{t('nomaflow.editor.userParams.fixedValues')}</Label>
                <StringListEditor value={p.choices ?? []} onChange={(choices) => set(i, { choices })} />
              </Cell>
            )}
            <Row>
              <Cell>
                <Label>{t('nomaflow.editor.userParams.default')}</Label>
                <Input value={p.default ?? ''} onChange={(e) => set(i, { default: e.target.value })} />
              </Cell>
              <Cell as="div">
                <Checkbox checked={!!p.required} onChange={(v) => set(i, { required: v })} label={t('nomaflow.editor.userParams.required')} />
              </Cell>
              <Cell as="div">
                <Checkbox checked={!!p.multiple} onChange={(v) => set(i, { multiple: v })} label={t('nomaflow.editor.userParams.multiple')} />
                <Hint>{t('nomaflow.editor.userParams.multipleHint')}</Hint>
              </Cell>
            </Row>
          </Box>
        )
      })}
      <div>
        <Button $variant="ghost" $size="sm" onClick={() => onChange([...value, { name: '' }])}><Plus size={13} /> {t('nomaflow.editor.userParams.add')}</Button>
      </div>
    </div>
  )
}
