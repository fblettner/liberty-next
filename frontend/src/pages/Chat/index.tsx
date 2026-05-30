import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Sparkles, Send, ArrowRight, Check, X, FileEdit, AlertTriangle } from 'lucide-react'
import { ApiError, api, streamSSE } from '../../api/client'
import type { AiTool, ChatEvent, ChatMessage, Proposal } from '../../types/ai'
import { PageLayout, Button, Banner, LinkButton, SpinnerRing, Mono, Tag } from '../../common'
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
`

const Composer = styled.form`
  display: flex;
  gap: 8px;
  align-items: flex-end;
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

export default function Chat() {
  const { t } = useTranslation()
  const [available, setAvailable] = useState<boolean | null>(null)
  const [model, setModel] = useState('')
  const [toolNames, setToolNames] = useState<string[]>([])
  const [entries, setEntries] = useState<Entry[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const historyRef = useRef<ChatMessage[]>([])

  useEffect(() => {
    api
      .get<{ available: boolean; model: string; tools: AiTool[] }>('/ai/tools')
      .then((r) => {
        setAvailable(r.available)
        setModel(r.model)
        setToolNames(r.tools.map((x) => x.name))
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [entries])

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setError(null)
    setBusy(true)
    historyRef.current = [...historyRef.current, { role: 'user', content: text }]
    setEntries((es) => [...es, { kind: 'msg', role: 'user', text }, { kind: 'msg', role: 'assistant', text: '' }])

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

    try {
      await streamSSE('/ai/chat', { messages: historyRef.current }, (raw) => {
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
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      historyRef.current = [...historyRef.current, { role: 'assistant', content: assistantText }]
      setBusy(false)
    }
  }

  // Send a synthetic user turn — used after Apply / Skip on a proposal so the assistant
  // re-reads state and continues the chain. Same plumbing as `send`, but the input box
  // doesn't show the message (it's a control-plane turn, not the operator's words).
  async function sendSynthetic(text: string) {
    if (busy) return
    setError(null); setBusy(true)
    historyRef.current = [...historyRef.current, { role: 'user', content: text }]
    setEntries((es) => [...es, { kind: 'msg', role: 'assistant', text: '' }])
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
    try {
      await streamSSE('/ai/chat', { messages: historyRef.current }, (raw) => {
        const ev = raw as ChatEvent
        if (ev.type === 'token') appendAssistant(ev.text ?? '')
        else if (ev.type === 'tool_call') addToolCall(ev.name ?? 'tool', ev.summary ?? '')
        else if (ev.type === 'tool_result') settleToolCall(ev.name ?? 'tool', !!ev.ok, ev.ok ? undefined : ev.summary)
        else if (ev.type === 'proposal' && ev.data) addProposal(ev.name ?? 'tool', ev.data)
        else if (ev.type === 'error') {
          setError(ev.message ?? 'error')
          appendAssistant(assistantText ? '' : t('chat.noResponse'))
        }
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      historyRef.current = [...historyRef.current, { role: 'assistant', content: assistantText }]
      setBusy(false)
    }
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
      await sendSynthetic(`Applied: ${toolName} (${r.summary}). Continue with the next step.`)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      updateProposal(proposal, { state: 'error', error: msg })
    }
  }

  function skipProposal(toolName: string, proposal: Proposal) {
    updateProposal(proposal, { state: 'skipped' })
    void sendSynthetic(`Skipped: ${toolName}. Do not retry this step; either move on or wait for new instructions.`)
  }

  function reset() {
    historyRef.current = []
    setEntries([])
    setError(null)
  }

  const description =
    available === null ? (
      '…'
    ) : available ? (
      <>
        {t('chat.model')}: <Mono>{model}</Mono> · {t('chat.tools')}: {toolNames.join(', ') || t('common.none')}
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
        entries.length > 0 ? (
          <HeaderRight>
            <LinkButton onClick={reset}>{t('chat.newConversation')}</LinkButton>
          </HeaderRight>
        ) : undefined
      }
    >
      <ChatArea>
        <Messages ref={scrollRef}>
          {entries.length === 0 && <Intro>{t('chat.intro')}</Intro>}
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
          <Button $variant="primary" type="submit" disabled={!available || busy || !input.trim()}>
            {busy ? <SpinnerRing size={14} thickness={2} /> : <Send size={14} />}
            {t('common.send')}
          </Button>
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
