// ActionPromptDialog — the small sub-dialog shown *before* an action with `prompt_fields` fires.
// v2's port of v1's `ly_act_params` flow: declare the inputs the action needs (a JDE "Create Role"
// workflow asks for AUUSER / JOBN / MUSE / PID / UPMJ), open this dialog, collect the values,
// merge them into the chain's resolution context — then the action's `param_binds` (and every
// later action in the same chain) can pick them up via `source: "<NAME>"`.
//
// Implementation notes:
//   - Widgets come from `FieldRow` — we synthesize a `Column` per prompt field whose `rule`/`format`
//     match what the screens API resolved (PromptField.rule mirrors Column.rule's shape). That keeps
//     the widget switch DRY between read-result cells, dialog fields, and prompt fields.
//   - Conditional rules (`visible_when` / `required_when` / `disabled_when`) evaluate against the
//     prompt's own local form state — so a JDE param shown only when another JDE param is "AC"
//     stays consistent inside the sub-dialog.
//   - Cancel resolves the chain with `null` → the chain runner treats that as a soft cancel and
//     aborts the rest of the chain without surfacing an error (same convention `ConfirmAction`
//     will use when wired).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { X, Check } from 'lucide-react'
import { Banner, Button, Modal, ModalBody, ModalFooter, ModalHeader, NestedOverlay, Row as FlexRow } from '../../common'
import type { Column } from '../../types/connectors'
import type { PromptField, ScreenField } from '../../types/screens'
import { evalConditions, type Row } from './dialogHelpers'
import { CellWrap, FieldRow } from './FieldRow'

const PromptGrid = styled.div<{ $cols: number }>`
  display: grid; grid-template-columns: repeat(${({ $cols }) => $cols}, 1fr); gap: 12px;
`

/** Build a synthetic Column from a resolved PromptField so FieldRow picks the right widget.
 *  The PromptField's `rule` already mirrors a Column's rule shape (the screens API resolved it
 *  against the dictionary); we just have to add the matching `name` / `type` / `format` keys. */
function columnFor(pf: PromptField): Column {
  return {
    name: pf.name,
    type: null,
    label: pf.label ?? undefined,
    format: pf.format ?? undefined,
    rule: pf.rule ?? undefined,
  }
}

export function ActionPromptDialog({
  open, title, fields, cols, submitLabel, onSubmit, onCancel,
}: {
  open: boolean
  title: string
  fields: PromptField[]
  cols?: number | null
  submitLabel?: string | null
  /** Resolves with the collected values keyed by `PromptField.name`. */
  onSubmit: (values: Row) => void
  /** Resolves with `null` — the chain runner treats this as a soft cancel. */
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Row>({})

  // Seed defaults on (re-)open. We also clear stale state so a second prompt in the same session
  // doesn't carry over values from a previous fire.
  useEffect(() => {
    if (!open) return
    const seeded: Row = {}
    for (const f of fields) {
      if (f.default != null && f.default !== '') seeded[f.name] = f.default
    }
    setValues(seeded)
  }, [open, fields])

  const onFieldChange = useCallback((name: string, v: unknown) => {
    setValues((p) => ({ ...p, [name]: v }))
  }, [])

  const gridCols = useMemo(() => Math.max(1, cols ?? 2), [cols])

  // Resolve per-field effective hidden / required / disabled per render — same evaluator the
  // ScreenDialog uses for ScreenField.*_when. Evaluates against the prompt's *own* form state.
  const fieldStateOf = useCallback((f: PromptField) => {
    const visibleByRule = (f.visible_when?.length ?? 0) > 0
      ? evalConditions(f.visible_when, values)
      : !f.hidden
    const requiredByRule = (f.required_when?.length ?? 0) > 0
      ? evalConditions(f.required_when, values)
      : !!f.required
    const disabledByRule = (f.disabled_when?.length ?? 0) > 0
      ? evalConditions(f.disabled_when, values)
      : !!f.disabled
    return { visible: visibleByRule, required: requiredByRule, disabled: disabledByRule }
  }, [values])

  const visibleFields = useMemo(() => fields.filter((f) => fieldStateOf(f).visible), [fields, fieldStateOf])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { if (open) setError(null) }, [open])

  const handleSubmit = useCallback(() => {
    // Required-field validation — check the *visible* + currently required ones (a hidden field
    // can't be required from the user's POV; a disabled one carries a default and is allowed to
    // pass through). Show a single, focused error instead of a per-field shake; the prompt is
    // small enough that one banner above the grid is enough.
    for (const f of visibleFields) {
      const st = fieldStateOf(f)
      if (!st.required) continue
      const v = values[f.name]
      if (v == null || v === '') {
        setError(t('prompt.required', { defaultValue: 'Please fill all required fields.' }))
        return
      }
    }
    onSubmit(values)
  }, [visibleFields, fieldStateOf, values, onSubmit, t])

  if (!open) return null
  return (
    <NestedOverlay onClick={onCancel} style={{ zIndex: 550 }}>
      <Modal style={{ width: 'min(600px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{title}</ModalHeader>
        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          <PromptGrid $cols={gridCols}>
            {visibleFields.map((f) => {
              const st = fieldStateOf(f)
              // Synthesize a Column so FieldRow's existing widget switch (BOOLEAN / ENUM / LOOKUP
              // / password / number / date / text) picks the right input. Cast the PromptField as
              // ScreenField — they're structurally compatible for FieldRow's needs (name + dd +
              // label + lookup_param_binds + colspan).
              const col = columnFor(f)
              return (
                <FieldRow
                  key={f.name}
                  field={f as unknown as ScreenField}
                  column={col}
                  formValues={values}
                  onChange={onFieldChange}
                  disabled={st.disabled}
                  required={st.required}
                />
              )
            })}
            {visibleFields.length === 0 && (
              <CellWrap $span={gridCols}>
                <Banner $tone="info">{t('prompt.noFields', { defaultValue: 'No inputs required — click Confirm to continue.' })}</Banner>
              </CellWrap>
            )}
          </PromptGrid>
        </ModalBody>
        <ModalFooter>
          <FlexRow gap={8}>
            <Button $size="sm" $variant="ghost" onClick={onCancel}>
              <X size={13} /> {t('common.cancel')}
            </Button>
            <Button $size="sm" $variant="primary" onClick={handleSubmit}>
              <Check size={13} /> {submitLabel || t('common.confirm', { defaultValue: 'Confirm' })}
            </Button>
          </FlexRow>
        </ModalFooter>
      </Modal>
    </NestedOverlay>
  )
}
