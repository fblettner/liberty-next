// "Insert a placeholder" for a templated text field (a notification's subject / body, a notify
// step's subject): a small { } button opening a searchable list of tokens; a pick splices the token
// (``{NAME}`` by default, ``wrap`` to change it) into the field at the caret — replacing the
// selection — and puts the caret after it. Ported from nomaubl, without its invoice vocabulary.
import { useRef, useState, type RefObject } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Braces } from 'lucide-react'
import { PortalPanel } from './PortalPanel'
import { insertAt, type Placeholder } from '../services/placeholders'
import { colors, fontSize, fonts, radius } from '../theme'

const Btn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; flex: none; cursor: pointer;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.secondary};
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.border}; }
`
const Search = styled.input`
  border: none; border-bottom: 1px solid ${colors.border}; background: transparent; outline: none; padding: 8px 12px;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
`
const List = styled.div`max-height: 280px; overflow-y: auto; padding: 4px 0;`
const Item = styled.button`
  display: flex; gap: 10px; align-items: baseline; width: 100%; padding: 6px 12px; border: none; background: none; cursor: pointer; text-align: left;
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; color: ${colors.text.secondary};
  & .n { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  &:hover, &:focus-visible { background: var(--hover-subtle); outline: none; }
`

export function PlaceholderPicker({ options, value, onChange, targetRef, wrap = (n) => `{${n}}` }: {
  options: Placeholder[]
  value: string
  onChange: (v: string) => void
  /** The input / textarea the token goes into (its caret and selection). */
  targetRef: RefObject<HTMLInputElement | HTMLTextAreaElement | null>
  wrap?: (name: string) => string
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const btnRef = useRef<HTMLButtonElement>(null)
  // the selection when the button was pressed (the field loses it to the popover)
  const sel = useRef<{ start: number | null; end: number | null }>({ start: null, end: null })
  const shown = options.filter((o) => `${o.name} ${o.label}`.toLowerCase().includes(q.trim().toLowerCase()))
  const pick = (name: string) => {
    const r = insertAt(value, sel.current.start, sel.current.end, wrap(name))
    onChange(r.text)
    setOpen(false); setQ('')
    requestAnimationFrame(() => {
      const el = targetRef.current
      if (el) { el.focus(); el.setSelectionRange(r.caret, r.caret) }
    })
  }
  return (
    <>
      <Btn ref={btnRef} type="button" title={t('common.insertPlaceholder')} aria-label={t('common.insertPlaceholder')}
        onMouseDown={() => { const el = targetRef.current; sel.current = { start: el?.selectionStart ?? null, end: el?.selectionEnd ?? null } }}
        onClick={() => setOpen((o) => !o)}>
        <Braces size={14} />
      </Btn>
      {open && (
        <PortalPanel triggerRef={btnRef} onDismiss={() => { setOpen(false); setQ('') }} minWidth={260}>
          <Search autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('table.search')}
            onKeyDown={(e) => { if (e.key === 'Enter' && shown[0]) { e.preventDefault(); pick(shown[0].name) } if (e.key === 'Escape') setOpen(false) }} />
          <List>
            {shown.map((o) => (
              <Item key={o.name} type="button" onClick={() => pick(o.name)}>
                <span className="n">{wrap(o.name)}</span><span>{o.label}</span>
              </Item>
            ))}
          </List>
        </PortalPanel>
      )}
    </>
  )
}
