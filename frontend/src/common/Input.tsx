import { useState, type ComponentProps, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { Eye, EyeOff } from 'lucide-react'
import { colors, radius, fontSize, fonts, shadow } from '../theme'

const fieldBase = `
  height: 32px;
  padding: 0 10px;
  border-radius: ${radius.md};
  border: 1px solid ${colors.border};
  background: ${colors.bg.input};
  color: ${colors.text.primary};
  font-size: ${fontSize.base};
  font-family: ${fonts.sans};
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  &:focus { border-color: ${colors.blue.main}; box-shadow: ${shadow.focus}; }
  &::placeholder { color: ${colors.text.muted}; }
`

export const Input = styled.input`
  ${fieldBase}
  width: 100%;
  &:read-only { background: var(--bg-readonly); color: ${colors.text.muted}; }
`

export const Select = styled.select`
  ${fieldBase}
  cursor: pointer;
`

export const Textarea = styled.textarea`
  ${fieldBase}
  height: auto;
  min-height: 80px;
  padding: 8px 10px;
  width: 100%;
  resize: vertical;
  line-height: 1.5;
`

export const FieldLabel = styled.label`
  display: block;
  font-size: ${fontSize.micro};
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: ${colors.text.muted};
  margin-bottom: 5px;
`

// A masked input with a reveal-eye toggle — used by SchemaForm for any field flagged
// `Field(json_schema_extra={"format": "password"})` (pool password, API connector auth secrets, …).
// The value still flows through the same string-field code path; this is purely a visual mask.
const PwdWrap = styled.div`position: relative; width: 100%;`
const RevealBtn = styled.button`
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px;
  border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; border-radius: ${radius.sm};
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`
export function PasswordInput(props: ComponentProps<typeof Input>) {
  const [show, setShow] = useState(false)
  return (
    <PwdWrap>
      <Input {...props} type={show ? 'text' : 'password'} style={{ paddingRight: 30, ...props.style }} />
      <RevealBtn type="button" onClick={() => setShow((s) => !s)} title={show ? 'Hide' : 'Reveal'} aria-label={show ? 'Hide' : 'Reveal'}>
        {show ? <EyeOff size={13} /> : <Eye size={13} />}
      </RevealBtn>
    </PwdWrap>
  )
}

/** A labelled form field — the uppercase label above whatever control you pass. */
export function Field({ label, children, htmlFor }: { label: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div>
      <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>
      {children}
    </div>
  )
}
