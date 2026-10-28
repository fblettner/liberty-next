// Several values out of a short choice list (the applications of an app…): a wrapping row of
// checkboxes headed by "All". The value is a list of picked values or 'all' (services/multiChoice).
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Checkbox } from './Checkbox'
import { colors, fontSize } from '../theme'
import { isPicked, toggleChoice, type MultiValue } from '../services/multiChoice'

const Wrap = styled.div`
  display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; padding: 6px 0;
  & .all { padding-right: 12px; margin-right: 4px; border-right: 1px solid ${colors.border}; }
  & .hint { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
`

export function MultiChoice({ value, choices, onChange, disabled, emptyHint }: {
  value: MultiValue
  choices: { value: string; label: string }[]
  onChange: (v: MultiValue) => void
  disabled?: boolean
  /** Shown when nothing is ticked (what an empty pick means, e.g. "the job's default"). */
  emptyHint?: string
}) {
  const { t } = useTranslation()
  const all = choices.map((c) => c.value)
  const some = value !== 'all' && value.length > 0
  return (
    <Wrap>
      <span className="all">
        <Checkbox label={t('common.all')} checked={value === 'all'} indeterminate={some} disabled={disabled}
          onChange={(on) => onChange(on ? 'all' : [])} />
      </span>
      {choices.map((c) => (
        <Checkbox key={c.value} label={c.label} checked={isPicked(value, c.value)} disabled={disabled}
          onChange={() => onChange(toggleChoice(value, c.value, all))} />
      ))}
      {value !== 'all' && value.length === 0 && emptyHint && <span className="hint">{emptyHint}</span>}
    </Wrap>
  )
}
