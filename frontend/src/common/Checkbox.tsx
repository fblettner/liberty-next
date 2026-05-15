// A themed checkbox — a native `<input type="checkbox">` overlaid invisibly on top of a
// styled box, so the box picks up the liquid-glass look (rounded corners, theme-aware
// border/fill, focus ring) while the input itself receives the real clicks. The earlier
// pattern hid the input off-screen and relied on `<label>`'s click-fires-input mechanism,
// but that's brittle: `pointer-events: none` on the hidden input broke the forwarding in
// some browsers (user reported clicks not toggling state). Putting the input ON TOP of the
// box, sized 100%, opacity 0, is the bog-standard custom-checkbox pattern — clicks land on
// a real form control, keyboard a11y stays free, no synthesised events.
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
`

// Positioning context for the box + the overlaid input — both must lay on top of each other.
const BoxWrap = styled.span`
  position: relative; display: inline-flex; flex-shrink: 0; width: 16px; height: 16px;
`

// The native input, sized 100% of BoxWrap with opacity 0 — invisible but click-receiving.
// Keep margin: 0 so the input doesn't push the box layout around in some browsers.
const HiddenInput = styled.input`
  position: absolute; inset: 0; width: 100%; height: 100%;
  margin: 0; padding: 0; opacity: 0;
  cursor: inherit;
`

const Box = styled.span<{ $checked: boolean; $indeterminate: boolean }>`
  position: absolute; inset: 0;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: ${radius.sm};
  border: 1px solid ${({ $checked, $indeterminate }) =>
    $checked || $indeterminate ? colors.blue.main : colors.border};
  background: ${({ $checked, $indeterminate }) =>
    $checked || $indeterminate ? colors.blue.main : colors.bg.input};
  color: white;
  transition: background-color 0.15s, border-color 0.15s, box-shadow 0.15s;
  pointer-events: none;  /* clicks pass through to the input that sits on top */
  /* Focus ring on the box, driven by the input's :focus-visible — keeps the ring keyboard-only. */
  ${HiddenInput}:focus-visible + & { box-shadow: ${shadow.focus}; }
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
      <BoxWrap>
        <HiddenInput
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
      </BoxWrap>
      {label != null && <span>{label}</span>}
    </Wrap>
  )
})
