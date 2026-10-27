import { useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'
import { Sparkles, Send, ArrowRight, Check, X, FileEdit, AlertTriangle, Square, MapPin } from 'lucide-react'
import { ApiError, api, streamSSE } from '../../api/client'
import type { AiInfo, AiModel, ChatEvent, ChatMessage, Proposal } from '../../types/ai'
import { getPageContext, renderPageContext, subscribePageContext, summarizePageContext, type PageContext } from '../../services/aiContext'
import { loadHistory, loadModel, pickStarters, saveHistory, saveModel, trimHistory } from '../../services/aiChat'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { PageLayout, Button, Banner, LinkButton, SearchSelect, SpinnerRing, Mono, Tag } from '../../common'
import { Markdown } from '../../common/Markdown'
import { colors, fontSize, fonts, radius } from '../../theme'

type ProposalState = 'pending' | 'applying' | 'applied' | 'skipped' | 'error'
// One tool entry per `tool_call` event; the matching `tool_result` updates this entry's
// ok flag in place. We keep the call summary (e.g. ``introspect_table(connector=jdedwards
// table=F03B11)``) — it tells the operator what the assistant tried — but drop the JSON
// payload from the result; the model needs it, the operator doesn't. Errors keep their
// message since that's actionable.
type Entry =
  | { kind: 'msg'; role: 'user' | 'assistant'; text: string }
  | { kind: 'tool'; ok: boolean | null; name: string; callSummary: string; error?: string }
  | { kind: 'proposal'; toolName: string; proposal: Proposal; state: ProposalState; error?: string }

const HeaderRight = styled.div`
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
`

const ChatArea = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  max-width: 840px;
  margin: 0 auto;
  width: 100%;
`

const Messages = styled.div`
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 2px;
`

const Bubble = styled.div<{ $role: 'user' | 'assistant' }>`
  padding: 9px 12px;
  border-radius: ${radius.lg};
  word-break: break-word;
  font-size: ${fontSize.md};
  line-height: 1.55;
  max-width: 88%;
  white-space: ${({ $role }) => ($role === 'user' ? 'pre-wrap' : 'normal')};
  ${({ $role }) =>
    $role === 'user'
      ? `align-self: flex-end; background: ${colors.blue.bg}; border: 1px solid ${colors.blue.border}; color: ${colors.text.primary};`
      : `align-self: flex-start; background: var(--ghost-bg, ${colors.bg.card}); border: 1px solid ${colors.border}; color: ${colors.text.secondary};`}
`

const ToolLine = styled.div<{ $ok: boolean | null }>`
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: ${fontSize.sm};
  font-family: ${fonts.mono};
  color: ${({ $ok }) => ($ok === false ? colors.red.main : $ok === true ? colors.green.main : colors.text.muted)};
`

// Proposal card — surfaces a write the assistant wants the operator to apply. The chat
// holds proposals in a state machine (pending → applying → applied/error/skipped); the
// payload preview stays collapsed by default to keep the transcript readable.
const ProposalCard = styled.div<{ $state: ProposalState }>`
  align-self: stretch; max-width: 100%;
  border: 1px solid ${({ $state }) =>
    $state === 'applied' ? colors.green.border
    : $state === 'error' ? colors.red.border
    : $state === 'skipped' ? colors.border
    : colors.blue.border};
  background: ${({ $state }) =>
    $state === 'applied' ? colors.green.bg
    : $state === 'error' ? colors.red.bg
    : $state === 'skipped' ? colors.bg.input
    : colors.blue.bg};
  border-radius: ${radius.md};
  padding: 12px 14px;
  display: flex; flex-direction: column; gap: 8px;
  font-family: ${fonts.sans};
`
const ProposalHead = styled.div`
  display: flex; align-items: center; gap: 8px; font-size: ${fontSize.md}; color: ${colors.text.primary};
  & .summary { flex: 1; min-width: 0; font-weight: 500; }
`
const ProposalMeta = styled.div`
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: ${fontSize.sm}; color: ${colors.text.muted};
`
const ProposalNotes = styled.ul`
  margin: 0; padding-left: 16px;
  font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & li { margin: 2px 0; }
`
const ProposalActions = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap;`
const PayloadToggle = styled.button`
  background: transparent; border: 0; color: ${colors.blue.main}; cursor: pointer;
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; padding: 0;
  &:hover { text-decoration: underline; }
`
const PayloadPre = styled.pre`
  margin: 0; padding: 8px 10px; border-radius: ${radius.sm};
  background: ${colors.bg.input}; border: 1px solid ${colors.border};
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.secondary};
  max-height: 280px; overflow: auto; white-space: pre-wrap; word-break: break-word;
`

const Intro = styled.div`
  color: ${colors.text.muted};
  font-size: ${fontSize.md};
  margin: auto;
  text-align: center;
  max-width: 520px;
  display: flex; flex-direction: column; gap: 14px; align-items: center;
`

// Starter questions — the operator's ``[ai] starters`` for this page; a click sends it.
const Starters = styled.div`
  display: flex; flex-direction: column; gap: 6px; align-items: stretch; width: 100%;
  & .title { font-size: ${fontSize.sm}; color: ${colors.text.muted}; }
`
const StarterBtn = styled.button`
  text-align: left; padding: 8px 12px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${colors.border}; background: var(--ghost-bg, ${colors.bg.card}); color: ${colors.text.primary};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; line-height: 1.4;
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`

const Composer = styled.form`
  display: flex;
  gap: 8px;
  align-items: flex-end;
`

// The page-context chip — what the next message carries about the page the operator is on.
// The × drops it for this page (it comes back when they move to another one).
const ContextChip = styled.div`
  display: inline-flex; align-items: center; gap: 6px; align-self: flex-start;
  padding: 3px 6px 3px 8px; border-radius: ${radius.xl};
  border: 1px solid ${colors.blue.border}; background: ${colors.blue.bg}; color: ${colors.text.secondary};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; max-width: 100%;
  & .name { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & button {
    display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; padding: 0;
    border: 0; border-radius: ${radius.xl}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
    &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
  }
`

const InputBox = styled.textarea`
  flex: 1;
  resize: none;
  min-height: 42px;
  max-height: 160px;
  padding: 10px 12px;
  border-radius: ${radius.md};
  border: 1px solid ${colors.border};
  background: ${colors.bg.input};
  color: ${colors.text.primary};
  font-size: ${fontSize.md};
  font-family: ${fonts.sans};
  line-height: 1.5;
  outline: none;
  &:focus { border-color: ${colors.blue.main}; }
  &::placeholder { color: ${colors.text.muted}; }
`

/** The transcript rebuilt from a persisted history: the message bubbles only (tool lines and
 *  proposal cards are not kept — the model's view of them lives in the assistant text). */
function entriesOf(messages: ChatMessage[]): Entry[] {
  return messages.map((m) => ({ kind: 'msg', role: m.role, text: m.content }))
}

export default function Chat() {
  const { t } = useTranslation()
  const { currentApp } = useWorkspace()
  const [available, setAvailable] = useState<boolean | null>(null)
  const [defaultModel, setDefaultModel] = useState('')
  const [models, setModels] = useState<AiModel[]>([])
  const [starters, setStarters] = useState<Record<string, string[]>>({})
  const [toolNames, setToolNames] = useState<string[]>([])
  // The conversation survives the drawer's close and a reload (services/aiChat, last 40 messages).
  const historyRef = useRef<ChatMessage[]>(loadHistory())
  const [entries, setEntries] = useState<Entry[]>(() => entriesOf(historyRef.current))
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // The in-flight /ai/chat stream. Stop (or leaving the page) aborts the fetch — the server
  // sees the connection close and the assistant loop stops at its next event.
  const abortRef = useRef<AbortController | null>(null)
  const stop = () => { abortRef.current?.abort(); abortRef.current = null }
  useEffect(() => () => abortRef.current?.abort(), [])
  const [error, setError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // The model picked on this device — only offered when the operator allowed more than one;
  // a pick that is no longer allowed falls back to the configured model.
  const [pickedModel, setPickedModel] = useState<string | null>(loadModel)
  const model = pickedModel && models.some((m) => m.id === pickedModel) ? pickedModel : defaultModel
  // The page context (services/aiContext): shown as a chip and sent with each turn while the
  // operator is still on the page that published it; the chip's × drops it for that page.
  const location = useLocation()
  const [pageCtx, setPageCtx] = useState<PageContext | null>(getPageContext)
  const [droppedPath, setDroppedPath] = useState<string | null>(null)
  useEffect(() => subscribePageContext(setPageCtx), [])
  const liveCtx = pageCtx && pageCtx.path === location.pathname && droppedPath !== pageCtx.path ? pageCtx : null
  const contextText = () => { const c = getPageContext(); return c && c.path === location.pathname && droppedPath !== c.path ? renderPageContext(c) : undefined }
  // Starters follow the page: its app + screen, else the workspace's app, else "everywhere".
  const onPage = pageCtx && pageCtx.path === location.pathname ? pageCtx : null
  const pageStarters = useMemo(() => pickStarters(starters, onPage?.app ?? currentApp, onPage?.screen ?? null), [starters, onPage, currentApp])

  useEffect(() => {
    api
      .get<AiInfo>('/ai/tools')
      .then((r) => {
        setAvailable(r.available)
        setDefaultModel(r.model)
        setModels(r.models ?? [])
        setStarters(r.starters ?? {})
        setToolNames(r.tools.map((x) => x.name))
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [entries])

  // One turn: the user text goes to the history (and, when ``show``, to the transcript), the
  // stream fills the assistant bubble. ``show = false`` is the control-plane turn sent after
  // Apply / Skip on a proposal — the assistant re-reads state and continues; the operator
  // never typed it, so the transcript does not show it.
  async function runTurn(text: string, show: boolean) {
    if (busy) return
    setError(null); setBusy(true)
    historyRef.current = [...historyRef.current, { role: 'user', content: text }]
    setEntries((es) => [...es, ...(show ? [{ kind: 'msg', role: 'user', text } as Entry] : []), { kind: 'msg', role: 'assistant', text: '' }])

    let assistantText = ''
    const appendAssistant = (chunk: string) => {
      assistantText += chunk
      setEntries((es) => {
        const copy = es.slice()
        for (let i = copy.length - 1; i >= 0; i--) {
          const e = copy[i]
          if (e.kind === 'msg' && e.role === 'assistant') {
            copy[i] = { kind: 'msg', role: 'assistant', text: assistantText }
            break
          }
        }
        return copy
      })
    }
    const addToolCall = (name: string, summary: string) =>
      setEntries((es) => [...es, { kind: 'tool', ok: null, name, callSummary: summary }])
    // Flip the LAST pending tool entry with this name. The assistant emits tool_call
    // strictly before its matching tool_result and never two pending calls of the same
    // name without a result in between, so "last pending with this name" is unambiguous.
    const settleToolCall = (name: string, ok: boolean, errorMsg?: string) =>
      setEntries((es) => {
        for (let i = es.length - 1; i >= 0; i--) {
          const e = es[i]
          if (e.kind === 'tool' && e.ok === null && e.name === name) {
            const copy = es.slice()
            copy[i] = { ...e, ok, error: errorMsg }
            return copy
          }
        }
        return es
      })
    const addProposal = (toolName: string, proposal: Proposal) => setEntries((es) => [
      ...es,
      { kind: 'proposal', toolName, proposal, state: 'pending' },
    ])

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamSSE('/ai/chat', {
        messages: trimHistory(historyRef.current), context: contextText(), model: model && model !== defaultModel ? model : undefined,
      }, (raw) => {
        const ev = raw as ChatEvent
        if (ev.type === 'token') appendAssistant(ev.text ?? '')
        else if (ev.type === 'tool_call') addToolCall(ev.name ?? 'tool', ev.summary ?? '')
        else if (ev.type === 'tool_result') settleToolCall(ev.name ?? 'tool', !!ev.ok, ev.ok ? undefined : ev.summary)
        else if (ev.type === 'proposal' && ev.data) addProposal(ev.name ?? 'tool', ev.data)
        else if (ev.type === 'error') {
          setError(ev.message ?? 'error')
          appendAssistant(assistantText ? '' : t('chat.noResponse'))
        }
        // "thinking" / "done" — ignored for display
      }, controller.signal)
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') appendAssistant(assistantText ? '' : t('chat.stopped'))
      else setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      historyRef.current = trimHistory([...historyRef.current, { role: 'assistant', content: assistantText }])
      saveHistory(historyRef.current)
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
    }
  }

  async function send(preset?: string) {
    const text = (preset ?? input).trim()
    if (!text || busy) return
    setInput('')
    await runTurn(text, true)
  }

  // Mutate a proposal entry's state in place (the operator clicked Apply / Skip on it).
  const updateProposal = (proposal: Proposal, patch: Partial<Extract<Entry, { kind: 'proposal' }>>) =>
    setEntries((es) => es.map((e) =>
      e.kind === 'proposal' && e.proposal === proposal ? { ...e, ...patch } : e))

  async function applyProposal(toolName: string, proposal: Proposal) {
    updateProposal(proposal, { state: 'applying', error: undefined })
    try {
      const r = await api.post<{ applied: boolean; kind: string; summary: string }>(
        '/admin/config/apply-proposal',
        {
          kind: proposal.kind, summary: proposal.summary, target_path: proposal.target_path,
          payload: proposal.payload, app: proposal.app, connector: proposal.connector,
        },
      )
      updateProposal(proposal, { state: 'applied' })
      // Tell the assistant the apply landed so it moves to the next step in the chain.
      await runTurn(`Applied: ${toolName} (${r.summary}). Continue with the next step.`, false)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      updateProposal(proposal, { state: 'error', error: msg })
    }
  }

  function skipProposal(toolName: string, proposal: Proposal) {
    updateProposal(proposal, { state: 'skipped' })
    void runTurn(`Skipped: ${toolName}. Do not retry this step; either move on or wait for new instructions.`, false)
  }

  function reset() {
    historyRef.current = []
    saveHistory([])
    setEntries([])
    setError(null)
  }

  const pickModel = (id: string) => { setPickedModel(id); saveModel(id === defaultModel ? null : id) }

  const description =
    available === null ? (
      '…'
    ) : available ? (
      <>
        {t('chat.model')}: <Mono>{model || defaultModel}</Mono> · {t('chat.tools')}: {toolNames.join(', ') || t('common.none')}
      </>
    ) : (
      <span style={{ color: colors.red.main }}>{t('chat.unavailable')}</span>
    )

  return (
    <PageLayout
      icon={<Sparkles size={18} />}
      title={t('chat.title')}
      description={description}
      headerRight={
        <HeaderRight>
          {models.length > 1 && (
            <div style={{ minWidth: 150 }} title={t('chat.pickModel')}>
              <SearchSelect value={model} options={models.map((m) => ({ value: m.id, label: m.label, mono: m.id }))} onChange={(v) => { if (v) pickModel(v) }} disabled={busy} />
            </div>
          )}
          {entries.length > 0 && <LinkButton onClick={reset}>{t('chat.newConversation')}</LinkButton>}
        </HeaderRight>
      }
    >
      <ChatArea>
        <Messages ref={scrollRef}>
          {entries.length === 0 && (
            <Intro>
              <span>{t('chat.intro')}</span>
              {available && pageStarters.length > 0 && (
                <Starters>
                  <span className="title">{t('chat.starters')}</span>
                  {pageStarters.map((q) => <StarterBtn key={q} type="button" onClick={() => void send(q)}>{q}</StarterBtn>)}
                </Starters>
              )}
            </Intro>
          )}
          {entries.map((e, i) =>
            e.kind === 'tool' ? (
              <ToolLine key={i} $ok={e.ok}>
                {e.ok === null ? <ArrowRight size={12} /> : e.ok ? <Check size={12} /> : <X size={12} />}
                <span>{e.name}{e.callSummary ? ` · ${e.callSummary}` : ''}{e.error ? ` — ${e.error}` : ''}</span>
              </ToolLine>
            ) : e.kind === 'proposal' ? (
              <ProposalEntry
                key={i}
                entry={e}
                onApply={() => void applyProposal(e.toolName, e.proposal)}
                onSkip={() => skipProposal(e.toolName, e.proposal)}
              />
            ) : (
              <Bubble key={i} $role={e.role}>
                {e.text ? (
                  e.role === 'assistant' ? (
                    <Markdown>{e.text}</Markdown>
                  ) : (
                    e.text
                  )
                ) : (
                  <span style={{ color: colors.text.muted }}>…</span>
                )}
              </Bubble>
            ),
          )}
        </Messages>
        {error && <Banner $tone="error">{error}</Banner>}
        {liveCtx && (() => {
          const s = summarizePageContext(liveCtx)
          const parts = [s.filters > 0 ? t('chat.contextFilters', { count: s.filters }) : null, s.hasRow ? t('chat.contextRow') : null].filter(Boolean)
          return (
            <ContextChip title={t('chat.contextHint')}>
              <MapPin size={12} />
              <span>{t('chat.context')}</span>
              <span className="name">{s.name}</span>
              {parts.length > 0 && <span>· {parts.join(' · ')}</span>}
              <button type="button" onClick={() => setDroppedPath(liveCtx.path)} title={t('chat.contextDrop')}><X size={11} /></button>
            </ContextChip>
          )
        })()}
        <Composer
          onSubmit={(ev) => {
            ev.preventDefault()
            void send()
          }}
        >
          <InputBox
            value={input}
            onChange={(ev) => setInput(ev.target.value)}
            onKeyDown={(ev) => {
              if (ev.key === 'Enter' && !ev.shiftKey) {
                ev.preventDefault()
                void send()
              }
            }}
            placeholder={available ? t('chat.placeholder') : t('chat.placeholderDisabled')}
            disabled={!available || busy}
          />
          {busy ? (
            <Button type="button" onClick={stop} title={t('chat.stopHint')}>
              <Square size={12} /> {t('common.stop')}
            </Button>
          ) : (
            <Button $variant="primary" type="submit" disabled={!available || !input.trim()}>
              <Send size={14} /> {t('common.send')}
            </Button>
          )}
        </Composer>
      </ChatArea>
    </PageLayout>
  )
}

// One proposal card — pending state shows Apply / Skip; once decided the card stays in the
// transcript with the outcome so the operator can scroll back and see what was applied. The
// payload preview is collapsed by default to keep the chat readable on long screens with
// many CRUD queries.
function ProposalEntry({
  entry, onApply, onSkip,
}: {
  entry: Extract<Entry, { kind: 'proposal' }>
  onApply: () => void
  onSkip: () => void
}) {
  const { t } = useTranslation()
  const [showPayload, setShowPayload] = useState(false)
  const { proposal, state, error, toolName } = entry
  const decided = state === 'applied' || state === 'skipped'
  const KindIcon = state === 'applied' ? Check : state === 'error' ? AlertTriangle : FileEdit
  return (
    <ProposalCard $state={state}>
      <ProposalHead>
        <KindIcon size={16} />
        <span className="summary">{proposal.summary}</span>
        <Tag $tone={state === 'applied' ? 'green' : state === 'error' ? 'red' : state === 'skipped' ? 'neutral' : 'blue'}>
          {state === 'applied' ? t('chat.proposal.applied', 'applied')
            : state === 'skipped' ? t('chat.proposal.skipped', 'skipped')
            : state === 'error' ? t('chat.proposal.error', 'error')
            : state === 'applying' ? t('chat.proposal.applying', 'applying…')
            : t('chat.proposal.pending', 'pending')}
        </Tag>
      </ProposalHead>
      <ProposalMeta>
        <Mono>{toolName}</Mono>
        <span>·</span>
        <span>{proposal.kind}</span>
        <span>·</span>
        <Mono>{proposal.target_path}</Mono>
        {proposal.app && <><span>·</span><span>{t('chat.proposal.app', 'app:')} <Mono>{proposal.app}</Mono></span></>}
        {proposal.connector && proposal.connector !== proposal.app && (
          <><span>·</span><span>{t('chat.proposal.connector', 'conn:')} <Mono>{proposal.connector}</Mono></span></>
        )}
      </ProposalMeta>
      {proposal.notes && proposal.notes.length > 0 && (
        <ProposalNotes>
          {proposal.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ProposalNotes>
      )}
      <ProposalActions>
        <PayloadToggle onClick={() => setShowPayload((v) => !v)}>
          {showPayload ? t('chat.proposal.hidePayload', 'Hide payload') : t('chat.proposal.showPayload', 'Show payload')}
        </PayloadToggle>
        {!decided && state !== 'applying' && (
          <>
            <span style={{ flex: 1 }} />
            <Button $variant="ghost" $size="sm" onClick={onSkip}>
              <X size={12} /> {t('chat.proposal.skip', 'Skip')}
            </Button>
            <Button $variant="primary" $size="sm" onClick={onApply}>
              <Check size={12} /> {t('chat.proposal.apply', 'Apply')}
            </Button>
          </>
        )}
        {state === 'applying' && (
          <>
            <span style={{ flex: 1 }} />
            <SpinnerRing size={13} thickness={2} />
          </>
        )}
      </ProposalActions>
      {showPayload && (
        <PayloadPre>{JSON.stringify(proposal.payload, null, 2)}</PayloadPre>
      )}
      {error && (
        <Banner $tone="error">{error}</Banner>
      )}
    </ProposalCard>
  )
}
