// The calculated-field editor: a name, a label, the expression (with insert-a-field and
// insert-a-function pickers that place the cursor inside the brackets) and a Test button that
// compiles it on the server (POST /api/explorer/{app}/calc/test) and shows the type and eight
// sample values. Save stays disabled until the test passes — the type decides measure / dimension.
import { useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { FlaskConical } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Field, Input, SearchSelect, SpinnerRing, type SearchSelectOption } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import type { CalcDef, CalcTestResult, ExplorerEntity, FieldType } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'

const Grid2 = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 12px;`
const Area = styled.textarea`
  width: 100%; min-height: 110px; resize: vertical; padding: 8px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; outline: none;
  &:focus { border-color: ${colors.blue.border}; }
`
const Help = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans}; line-height: 1.5;`
const Sample = styled.div`display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: ${fontSize.sm}; font-family: ${fonts.mono}; color: ${colors.text.secondary};
  & span.v { padding: 2px 6px; border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input}; }`

const FUNCTIONS = ['year(d)', 'month(d)', 'day(d)', 'days(from, to)', 'round(x, n)', 'abs(x)', 'coalesce(a, b)', 'upper(s)', 'lower(s)', 'length(s)', 'concat(a, b)']

export function CalcModal({ app, entity, primary, entities, calculated, initial, onSave, onClose }: {
  app: string
  entity: string
  primary: string
  entities: ExplorerEntity[]
  /** the other calculated fields of the definition (sent along so the test sees them all) */
  calculated: CalcDef[]
  initial: CalcDef | null
  onSave: (def: CalcDef, type: FieldType) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(initial?.name ?? '')
  const [label, setLabel] = useState(initial?.label ?? '')
  const [expr, setExpr] = useState(initial?.expr ?? '')
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<CalcTestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const area = useRef<HTMLTextAreaElement | null>(null)

  const fieldOpts = useMemo<SearchSelectOption[]>(() => entities.filter((e) => e.id !== 'calc').flatMap((e) => e.fields.map((f) => ({
    value: e.id === primary ? f.name : `${e.id}.${f.name}`, label: `${e.label} · ${f.label}`, mono: e.id === primary ? f.name : `${e.id}.${f.name}`,
  }))), [entities, primary])
  const funcOpts: SearchSelectOption[] = FUNCTIONS.map((f) => ({ value: f, label: f }))

  const insert = (text: string, cursorInside: boolean) => {
    const el = area.current
    const start = el?.selectionStart ?? expr.length
    const end = el?.selectionEnd ?? expr.length
    const next = expr.slice(0, start) + text + expr.slice(end)
    setExpr(next); setTested(null)
    const pos = cursorInside ? start + text.indexOf('(') + 1 : start + text.length
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(pos, cursorInside ? pos + (text.indexOf(')') - text.indexOf('(') - 1) : pos) })
  }
  const nameOk = /^[A-Za-z][A-Za-z0-9_]{0,40}$/.test(name) && !calculated.some((c) => c.name.toUpperCase() === name.toUpperCase() && c.name !== initial?.name)
  const test = async () => {
    setTesting(true); setError(null); setTested(null)
    try {
      const others = calculated.filter((c) => c.name !== initial?.name)
      const r = await api.post<CalcTestResult>(`/api/explorer/${encodeURIComponent(app)}/calc/test`, { entity, calculated: [...others, { name, label: label || undefined, expr }], name })
      setTested(r)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setTesting(false) }
  }

  return (
    <EditorModalShell title={initial ? `${t('explorer.calcField')} · ${initial.name}` : t('explorer.newCalcField')} onClose={onClose}
      dirty={!!expr} saveDisabled={!tested || !nameOk} onSave={() => tested && onSave({ name, label: label || undefined, expr }, tested.type)}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '4px 0' }}>
        <Grid2>
          <Field label={t('explorer.calcName')}>
            <Input value={name} onChange={(e) => { setName(e.target.value); setTested(null) }} placeholder="unit_price" />
            {name && !nameOk && <span style={{ color: colors.red.main, fontSize: fontSize.micro }}>{t('explorer.calcNameRule')}</span>}
          </Field>
          <Field label={t('explorer.label')}><Input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
        </Grid2>
        <Grid2>
          <Field label={t('explorer.insertField')}><SearchSelect value="" options={fieldOpts} placeholder={t('explorer.pickField')} onChange={(v) => v && insert(v, false)} /></Field>
          <Field label={t('explorer.insertFunction')}><SearchSelect value="" options={funcOpts} placeholder={t('explorer.pickFunction')} onChange={(v) => v && insert(v, true)} /></Field>
        </Grid2>
        <Field label={t('explorer.expression')}>
          <Area ref={area} value={expr} onChange={(e) => { setExpr(e.target.value); setTested(null) }} spellCheck={false} placeholder="REVENUE / QTY" />
        </Field>
        <Help>{t('explorer.calcHelp')}</Help>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Button $size="sm" $variant="ghost" onClick={() => void test()} disabled={testing || !expr.trim() || !nameOk}>
            {testing ? <SpinnerRing size={13} thickness={2} /> : <FlaskConical size={13} />} {t('explorer.test')}
          </Button>
          {tested && (
            <Sample>
              <span>{t(`explorer.calcTyped.${tested.kind}`, { type: tested.type })}</span>
              {tested.values.map((v, i) => <span className="v" key={i}>{v === null ? '—' : String(v)}</span>)}
            </Sample>
          )}
        </div>
        {error && <Banner $tone="error">{error}</Banner>}
      </div>
    </EditorModalShell>
  )
}
