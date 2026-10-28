// The App editor's two AI pickers: the models a user may choose in the chat (chips + the
// catalogue dropdown) and the starter questions per scope — ``*`` (everywhere), an app, or
// ``app/screen`` — each a list of sentences.
import { useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'
import { Button, Input, SearchSelect, Tag, type SearchSelectOption } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-height: 32px;`
const RemoveBtn = styled.button`
  display: inline-flex; align-items: center; margin-left: 4px; padding: 0; border: 0; background: transparent;
  color: inherit; cursor: pointer; opacity: 0.7; &:hover { opacity: 1; }
`
const Scope = styled.div`
  display: flex; flex-direction: column; gap: 6px; padding: 8px 10px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
`
const ScopeHead = styled.div`
  display: flex; align-items: center; gap: 8px; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & .key { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & .spacer { flex: 1; }
`
const Question = styled.div`
  display: flex; align-items: center; gap: 6px; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  & span { flex: 1; min-width: 0; }
`

export function ModelsPicker({ value, models, onChange }: {
  value: string[]
  models: { id: string; label: string }[]
  onChange: (next: string[]) => void
}) {
  const { t } = useTranslation()
  const remaining: SearchSelectOption[] = models.filter((m) => !value.includes(m.id)).map((m) => ({ value: m.id, label: m.label, mono: m.id }))
  const labelOf = (id: string) => models.find((m) => m.id === id)?.label ?? id
  return (
    <Chips>
      {value.map((id) => (
        <Tag key={id}>
          {labelOf(id)}
          <RemoveBtn type="button" onClick={() => onChange(value.filter((v) => v !== id))} aria-label="Remove"><X size={11} /></RemoveBtn>
        </Tag>
      ))}
      <div style={{ minWidth: 220 }}>
        <SearchSelect value="" options={remaining} placeholder={t('settings.app.aiAddModel')} allowCustom
          onChange={(v) => { if (v && !value.includes(v)) onChange([...value, v]) }} />
      </div>
    </Chips>
  )
}

/** One list of questions: each a line with its ×, plus an input to add one (Enter or +). */
export function QuestionsList({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState('')
  const add = () => {
    const q = draft.trim()
    if (!q) return
    if (!value.includes(q)) onChange([...value, q])
    setDraft('')
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {value.map((q) => (
        <Question key={q}>
          <span>{q}</span>
          <RemoveBtn type="button" onClick={() => onChange(value.filter((x) => x !== q))} aria-label="Remove"><X size={11} /></RemoveBtn>
        </Question>
      ))}
      <div style={{ display: 'flex', gap: 6 }}>
        <Input value={draft} placeholder={t('settings.app.aiStarterPlaceholder')}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} style={{ flex: 1 }} />
        <Button $variant="ghost" $size="sm" onClick={add} disabled={!draft.trim()}><Plus size={12} /></Button>
      </div>
    </div>
  )
}

export function AiStartersEditor({ value, apps, onChange }: {
  value: Record<string, string[]>
  /** The connector / app names the scope dropdown offers (``*`` is always offered; ``app/screen`` is typed). */
  apps: string[]
  onChange: (next: Record<string, string[]>) => void
}) {
  const { t } = useTranslation()
  const scopes = Object.keys(value)
  const scopeOptions: SearchSelectOption[] = [
    { value: '*', label: t('settings.app.aiStartersEverywhere'), mono: '*' },
    ...apps.map((a) => ({ value: a, label: a, mono: a })),
  ].filter((o) => !scopes.includes(o.value))
  const setScope = (key: string, list: string[]) => onChange({ ...value, [key]: list })
  const dropScope = (key: string) => { const next = { ...value }; delete next[key]; onChange(next) }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {scopes.map((key) => (
        <Scope key={key}>
          <ScopeHead>
            <span className="key">{key === '*' ? t('settings.app.aiStartersEverywhere') : key}</span>
            <span className="spacer" />
            <Button $variant="ghost" $size="sm" onClick={() => dropScope(key)} title={t('common.remove')}><X size={12} /></Button>
          </ScopeHead>
          <QuestionsList value={value[key] ?? []} onChange={(list) => setScope(key, list)} />
        </Scope>
      ))}
      <div style={{ maxWidth: 320 }}>
        <SearchSelect value="" options={scopeOptions} allowCustom placeholder={t('settings.app.aiStartersAddScope')}
          onChange={(v) => { const k = v.trim(); if (k && !scopes.includes(k)) setScope(k, []) }} />
      </div>
    </div>
  )
}
