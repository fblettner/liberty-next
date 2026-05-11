import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Sparkles, Send, ArrowRight, Check, X } from 'lucide-react'
import { ApiError, api, streamSSE } from '../api'
import type { AiTool, ChatEvent, ChatMessage } from '../types'
import { PageLayout, Button, Banner, LinkButton, SpinnerRing, Mono } from '../ui'
import { Markdown } from './Markdown'
import { colors, fontSize, fonts, radius } from '../theme'

type Entry =
  | { kind: 'msg'; role: 'user' | 'assistant'; text: string }
  | { kind: 'tool'; ok: boolean | null; text: string }

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

export function Chat() {
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
    const addTool = (ok: boolean | null, line: string) => setEntries((es) => [...es, { kind: 'tool', ok, text: line }])

    try {
      await streamSSE('/ai/chat', { messages: historyRef.current }, (raw) => {
        const ev = raw as ChatEvent
        if (ev.type === 'token') appendAssistant(ev.text ?? '')
        else if (ev.type === 'tool_call') addTool(null, `${ev.name}(${ev.summary ?? ''})`)
        else if (ev.type === 'tool_result') addTool(!!ev.ok, `${ev.name} → ${ev.summary ?? ''}`)
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
                {e.text}
              </ToolLine>
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
