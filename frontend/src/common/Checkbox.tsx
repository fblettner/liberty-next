// A themed checkbox — a native `<input type="checkbox">` hidden behind a styled
// `<span>` box, so it picks up the rest of the form's liquid-glass look (rounded
// corners, theme-aware border/fill, focus ring) instead of the OS chrome the
// browser would otherwise paint. Used everywhere boolean values are toggled
// (SchemaForm's boolean fields, ScreenDialog's BOOLEAN-ruled columns, the SQL
// wizard's column picker). The `<input>` itself stays in the DOM and keeps full
// keyboard semantics — space toggles, Tab focuses, the label is clickable.
import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { Check, Minus } from 'lucide-react'
import { colors, fontSize, fonts, radius, shadow } from '../theme'

const Wrap = styled.label<{ $disabled?: boolean }>`
  display: inline-flex; align-items: center; gap: 8px;
  cursor: ${({ $disabled }) => ($disabled ? 'not-allowed' : 'pointer')};
  opacity: ${({ $disabled }) => ($disabled ? 0.55 : 1)};
  font-family: ${fonts.sans}; font-size: ${fontSize.base}; color: ${colors.text.secondary};
  user-select: none;
  /* Hide the native input — we still keep it in the DOM so the keyboard / a11y
     behaviour is unchanged, but the visible box is the styled span below it. */
  & > input {
    position: absolute; opacity: 0; pointer-events: none; width: 0; height: 0;
  }
`

const Box = styled.span<{ $checked: boolean; $indeterminate: boolean }>`
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; flex-shrink: 0;
  border-radius: ${radius.sm};
  border: 1px solid ${({ $checked, $indeterminate }) =>
    $checked || $indeterminate ? colors.blue.main : colors.border};
  background: ${({ $checked, $indeterminate }) =>
    $checked || $indeterminate ? colors.blue.main : colors.bg.input};
  color: white;
  transition: background-color 0.15s, border-color 0.15s, box-shadow 0.15s;
  /* Focus ring lives on the box, driven by the visually-hidden input's :focus-visible
     state — a label > input + box sibling-selector keeps the ring keyboard-only. */
  ${Wrap}:has(input:focus-visible) & { box-shadow: ${shadow.focus}; }
  ${Wrap}:hover & { border-color: ${colors.blue.border}; }
`

export interface CheckboxProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'onChange' | 'size' | 'checked'> {
  /** True / false / null / undefined. `null` and `undefined` fall back to `false` so callers can
   *  pass loosely-typed values (e.g. a JSON Schema default that might be missing) without a guard. */
  checked?: boolean | null
  /** Visual third state (the inner icon switches to a horizontal bar) — useful for
   *  "some items selected" headers. Not surfaced by the SchemaForm path. */
  indeterminate?: boolean
  /** Receives the new checked state directly — saves callers writing `e.target.checked`. */
  onChange?: (checked: boolean) => void
  /** Optional inline label rendered to the right of the box. Pass plain text or a node. */
  label?: ReactNode
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { checked, indeterminate, onChange, label, disabled, className, style, ...rest },
  ref,
) {
  const isChecked = Boolean(checked)
  return (
    <Wrap $disabled={disabled} className={className} style={style}>
      <input
        ref={ref}
        type="checkbox"
        checked={isChecked}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.checked)}
        {...rest}
      />
      <Box $checked={isChecked} $indeterminate={Boolean(indeterminate && !isChecked)}>
        {indeterminate && !isChecked ? <Minus size={11} strokeWidth={3} /> : isChecked ? <Check size={11} strokeWidth={3} /> : null}
      </Box>
      {label != null && <span>{label}</span>}
    </Wrap>
  )
})
