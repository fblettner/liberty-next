// "Create from…" of the API connector editor: an endpoint written from what the connector's
// server describes (a report version with a variable per option, a table with its filters, an
// orchestration with a variable per input…).
//
// The logic is the application's — a plugin function declared in the connector's `generators`
// (liberty/connectors/config.py::EndpointGenerator) — called through POST /admin/config/api/generate
// in three steps: search → the candidates; options → what to ask about the one picked; build →
// the endpoint, handed to the editor which adds it UNSAVED for the operator to review.
// It runs against the SAVED connector (it needs its credentials).
import { useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Search, Wand2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Input, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SearchSelect, SearchSelectMulti, SpinnerRing,
} from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

export interface EndpointGeneratorDef {
  id: string
  label: string
  callable: string
  description?: string | null
}
interface Candidate { id: string; label?: string; description?: string }
interface OptionField {
  name: string
  label?: string
  kind?: 'text' | 'choice' | 'multi'
  choices?: { value: string; label?: string }[]
  default?: string | string[] | null
  help?: string | null
}

const Box = styled(Modal)`width: min(760px, 95vw);`
const Line = styled.div`display: flex; gap: 10px; align-items: center;`
const Label = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; letter-spacing: 0.5px; text-transform: uppercase; margin: 14px 0 6px;
`
const Help = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; margin-top: 4px; white-space: pre-wrap;`
const List = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; max-height: 260px; overflow: auto;
`
const Item = styled.button<{ $on: boolean }>`
  display: flex; gap: 10px; align-items: baseline; width: 100%; padding: 7px 12px; border: 0; cursor: pointer; text-align: left;
  background: ${({ $on }) => ($on ? colors.blue.bg : 'transparent')}; color: ${colors.text.primary}; font-size: ${fontSize.sm};
  border-bottom: 1px solid ${colors.border};
  &:last-of-type { border-bottom: 0; }
  &:hover { background: ${colors.blue.bg}; }
  & .code { font-family: ${fonts.mono}; flex-shrink: 0; }
  & .desc { color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`

export function EndpointGeneratorModal({ connector, generators, onCreate, onClose }: {
  connector: string
  generators: EndpointGeneratorDef[]
  /** The endpoint the generator built — the editor adds it, unsaved. */
  onCreate: (endpoint: Record<string, unknown>) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [genId, setGenId] = useState(generators[0]?.id ?? '')
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<Candidate[] | null>(null)
  const [picked, setPicked] = useState<Candidate | null>(null)
  const [fields, setFields] = useState<OptionField[]>([])
  const [note, setNote] = useState<string | null>(null)
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generator = generators.find((g) => g.id === genId)

  const step = async <T,>(name: string, extra: Record<string, unknown>): Promise<T | null> => {
    setBusy(true); setError(null)
    try {
      return await api.post<T>('/admin/config/api/generate', { connector, generator: genId, step: name, ...extra })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      return null
    } finally {
      setBusy(false)
    }
  }
  const reset = () => { setCandidates(null); setPicked(null); setFields([]); setNote(null); setAnswers({}) }
  const search = async () => {
    reset()
    const r = await step<{ candidates: Candidate[] }>('search', { query })
    if (r) setCandidates(r.candidates ?? [])
  }
  const pick = async (c: Candidate) => {
    setPicked(c); setFields([]); setNote(null); setAnswers({})
    const r = await step<{ options: OptionField[]; note?: string | null }>('options', { candidate: c.id })
    if (!r) return
    setFields(r.options ?? [])
    setNote(r.note ?? null)
    const init: Record<string, string | string[]> = {}
    for (const f of r.options ?? []) init[f.name] = f.default ?? (f.kind === 'multi' ? [] : '')
    setAnswers(init)
  }
  const create = async () => {
    if (!picked) return
    const r = await step<{ endpoint: Record<string, unknown> }>('build', { candidate: picked.id, options: answers })
    if (r?.endpoint) onCreate(r.endpoint)
  }

  // Portalled to document.body, as the shared confirm is: rendered inside the builder it sits in a
  // stacking context of its own (the panel's backdrop-filter) and a fixed overlay does not show.
  return createPortal(
    <Overlay onClick={onClose} style={{ zIndex: 600 }}>
      <Box onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('settings.api.generate.title')}</ModalHeader>
        <ModalBody>
          {generators.length > 1 && (
            <SearchSelect
              value={genId}
              options={generators.map((g) => ({ value: g.id, label: g.label }))}
              onChange={(v) => { setGenId(v ?? ''); setQuery(''); reset(); setError(null) }}
            />
          )}
          {generator?.description && <Help>{generator.description}</Help>}
          <Label>{t('settings.api.generate.search')}</Label>
          <Line>
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void search() }}
              placeholder={t('table.search')}
            />
            <Button $size="sm" onClick={() => void search()} disabled={busy}>
              {busy && !candidates ? <SpinnerRing size={13} thickness={2} /> : <Search size={13} />} {t('settings.api.generate.find')}
            </Button>
          </Line>
          {error && <div style={{ marginTop: 12 }}><Banner $tone="error">{error}</Banner></div>}
          {candidates && (
            <>
              <Label>{t('settings.api.generate.found', { count: candidates.length })}</Label>
              {candidates.length > 0 && (
                <List>
                  {candidates.map((c) => (
                    <Item key={c.id} type="button" $on={picked?.id === c.id} onClick={() => void pick(c)}>
                      <span className="code">{c.label ?? c.id}</span>
                      <span className="desc">{c.description}</span>
                    </Item>
                  ))}
                </List>
              )}
            </>
          )}
          {picked && note && <Help style={{ marginTop: 12 }}>{note}</Help>}
          {picked && fields.map((f) => (
            <div key={f.name}>
              <Label>{f.label ?? f.name}</Label>
              {f.kind === 'multi' ? (
                <SearchSelectMulti
                  value={Array.isArray(answers[f.name]) ? (answers[f.name] as string[]) : []}
                  options={(f.choices ?? []).map((c) => ({ value: c.value, label: c.label ?? c.value }))}
                  onChange={(v) => setAnswers((a) => ({ ...a, [f.name]: v }))}
                  maxChips={6}
                />
              ) : f.kind === 'choice' ? (
                <SearchSelect
                  value={typeof answers[f.name] === 'string' ? (answers[f.name] as string) : ''}
                  options={(f.choices ?? []).map((c) => ({ value: c.value, label: c.label ?? c.value }))}
                  onChange={(v) => setAnswers((a) => ({ ...a, [f.name]: v ?? '' }))}
                />
              ) : (
                <Input
                  value={typeof answers[f.name] === 'string' ? (answers[f.name] as string) : ''}
                  onChange={(e) => setAnswers((a) => ({ ...a, [f.name]: e.target.value }))}
                />
              )}
              {f.help && <Help>{f.help}</Help>}
            </div>
          ))}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button $variant="primary" onClick={() => void create()} disabled={!picked || busy}>
            {busy && picked ? <SpinnerRing size={13} thickness={2} /> : <Wand2 size={13} />} {t('settings.api.generate.create')}
          </Button>
        </ModalFooter>
      </Box>
    </Overlay>,
    document.body,
  )
}
