// "Create from…" of the API connector editor: an endpoint written from what the connector's
// server describes (a report version with a variable per option, a table with its filters, an
// orchestration with a variable per input…).
//
// The logic is the application's — a plugin function declared in the connector's `generators`
// (liberty/connectors/config.py::EndpointGenerator) — called through POST /admin/config/api/generate
// in three steps: search → the candidates; options → what to ask about the one picked; build →
// the endpoint, handed to the editor which adds it UNSAVED for the operator to review.
// It runs against the SAVED connector (it needs its credentials).
//
// A generator may build a whole SCREEN instead (the endpoints it reads and saves through, and the
// screen itself): the dialog then asks where to put it and creates everything in one pass through
// the Screen Assistant's route (POST /admin/assistant/scaffold) — saved, unlike a single endpoint.
import { useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Search, Wand2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Checkbox, Input, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SearchSelect, SearchSelectMulti, SpinnerRing,
} from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
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

export function EndpointGeneratorModal({ connector, generators, existingNames = [], onCreate, onClose }: {
  connector: string
  generators: EndpointGeneratorDef[]
  /** The names of the connector's endpoints: a screen's endpoint that already exists is kept or replaced. */
  existingNames?: string[]
  /** The endpoint(s) the generator built — the editor adds them: unsaved for a single endpoint,
   *  already *saved* for a screen's endpoints (created with it; *replaced* = the ones that existed
   *  were written again). */
  onCreate: (endpoints: Record<string, unknown>[], saved: boolean, replaced?: boolean) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { apps, currentApp, lastApp } = useWorkspace()
  const [genId, setGenId] = useState(generators[0]?.id ?? '')
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<Candidate[] | null>(null)
  const [picked, setPicked] = useState<Candidate | null>(null)
  const [fields, setFields] = useState<OptionField[]>([])
  const [note, setNote] = useState<string | null>(null)
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [replace, setReplace] = useState(false)
  const generator = generators.find((g) => g.id === genId)
  // a generator that built a whole screen: what it built, and where the operator puts it
  const [bundle, setBundle] = useState<{ endpoints: Record<string, unknown>[]; screen: Record<string, unknown>; note?: string | null } | null>(null)
  const [target, setTarget] = useState({ app: '', id: '', label: '', menu: true })
  const [done, setDone] = useState<string | null>(null)

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
    const r = await step<{ endpoint?: Record<string, unknown>; endpoints?: Record<string, unknown>[]; screen?: Record<string, unknown>; app?: string | null; note?: string | null }>(
      'build', { candidate: picked.id, options: answers })
    if (r?.screen && r.endpoints) {
      setBundle({ endpoints: r.endpoints, screen: r.screen, note: r.note })
      // the application the screen goes to: the generator's, else the one the operator works in
      const known = (apps ?? []).map((a) => a.name)
      const home = [r.app, currentApp, lastApp].find((a) => a && known.includes(a)) ?? known[0] ?? connector
      setTarget({ app: home, id: String(r.screen.id ?? ''), label: String(r.screen.label ?? ''), menu: true })
    } else if (r?.endpoints?.length) onCreate(r.endpoints, false)      // several endpoints, no screen: added to the list, unsaved
    else if (r?.endpoint) onCreate([r.endpoint], false)
  }
  const createScreen = async () => {
    if (!bundle) return
    const app = target.app.trim(), id = target.id.trim(), label = target.label.trim() || id
    setBusy(true); setError(null)
    try {
      await api.post('/admin/assistant/scaffold', {
        connector, app, endpoints: bundle.endpoints, screen: { ...bundle.screen, id, label },
        // one of them is already on the connector: kept as it is (the screen uses it), or written again
        existing_endpoints: replace ? 'replace' : 'keep',
        menu: target.menu ? { app, id, label, target: id, connector } : null,
      })
      setDone(`${app} / ${id}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const finish = () => { if (bundle && done) onCreate(bundle.endpoints, true, replace); else onClose() }
  const clashing = bundle ? bundle.endpoints.filter((e) => existingNames.includes(String(e.name))).length : 0

  // Portalled to document.body, as the shared confirm is: rendered inside the builder it sits in a
  // stacking context of its own (the panel's backdrop-filter) and a fixed overlay does not show.
  return createPortal(
    <Overlay onClick={finish} style={{ zIndex: 600 }}>
      <Box onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t(bundle ? 'settings.api.generate.screenTitle' : 'settings.api.generate.title')}</ModalHeader>
        {bundle && (
          <ModalBody>
            {bundle.note && <Help>{bundle.note}</Help>}
            <Label>{t('settings.api.generate.screenEndpoints')}</Label>
            <List>
              {bundle.endpoints.map((e) => (
                <Item key={String(e.name)} type="button" $on={false} disabled>
                  <span className="code">{String(e.name)}</span>
                  <span className="desc">
                    {existingNames.includes(String(e.name)) ? `${t(replace ? 'settings.api.generate.screenReplaced' : 'settings.api.generate.screenKept')} · ` : ''}
                    {String(e.description ?? e.label ?? '')}
                  </span>
                </Item>
              ))}
            </List>
            {clashing > 0 && (
              <div style={{ marginTop: 8 }}>
                <Checkbox checked={replace} disabled={!!done} onChange={(v: boolean) => setReplace(v)}
                  label={t('settings.api.generate.screenReplace', { n: clashing })} />
                <Help>{t('settings.api.generate.screenReplaceHint')}</Help>
              </div>
            )}
            <Label>{t('settings.api.generate.screenApp')}</Label>
            <SearchSelect
              value={target.app}
              disabled={!!done}
              options={[...new Set([...(apps ?? []).map((a) => a.name), target.app])].filter(Boolean).map((a) => ({ value: a, label: a }))}
              onChange={(v) => setTarget((s) => ({ ...s, app: v ?? '' }))}
            />
            <Label>{t('settings.api.generate.screenId')}</Label>
            <Input value={target.id} disabled={!!done} onChange={(e) => setTarget((s) => ({ ...s, id: e.target.value }))} />
            <Label>{t('settings.api.generate.screenLabel')}</Label>
            <Input value={target.label} disabled={!!done} onChange={(e) => setTarget((s) => ({ ...s, label: e.target.value }))} />
            <div style={{ marginTop: 14 }}>
              <Checkbox checked={target.menu} disabled={!!done} onChange={(v: boolean) => setTarget((s) => ({ ...s, menu: v }))} label={t('settings.api.generate.screenMenu')} />
            </div>
            {error && <div style={{ marginTop: 12 }}><Banner $tone="error">{error}</Banner></div>}
            {done && <div style={{ marginTop: 12 }}><Banner $tone="ok">{t('settings.api.generate.screenDone', { screen: done })}</Banner></div>}
          </ModalBody>
        )}
        {!bundle && <ModalBody>
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
        </ModalBody>}
        {bundle ? (
          <ModalFooter>
            {!done && <Button $variant="ghost" onClick={() => { setBundle(null); setError(null) }}>{t('settings.api.generate.screenBack')}</Button>}
            {done
              ? <Button $variant="primary" onClick={finish}>{t('common.close')}</Button>
              : (
                <Button $variant="primary" onClick={() => void createScreen()} disabled={busy || !target.app.trim() || !target.id.trim()}>
                  {busy ? <SpinnerRing size={13} thickness={2} /> : <Wand2 size={13} />} {t('settings.api.generate.screenCreate')}
                </Button>
              )}
          </ModalFooter>
        ) : (
          <ModalFooter>
            <Button $variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
            <Button $variant="primary" onClick={() => void create()} disabled={!picked || busy}>
              {busy && picked ? <SpinnerRing size={13} thickness={2} /> : <Wand2 size={13} />} {t('settings.api.generate.create')}
            </Button>
          </ModalFooter>
        )}
      </Box>
    </Overlay>,
    document.body,
  )
}
