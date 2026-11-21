// The studio's graphical view of a service (a shared action): its steps drawn as a flow read left to
// right — start (the inputs), each step a card, a condition forking into its True / False lanes, a loop
// or a group framing its own steps, the answer at the end. The layout follows the action's structure
// (a sequence of steps that may nest), so nothing is placed by hand: the picture is always the action.
// Click a card to edit it in the side panel; a "+" on every link inserts a step there.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import {
  Bell, Boxes, CornerDownLeft, Database, ExternalLink, FileText, Flag, GitBranch, Globe, LayoutList, Link2, MessageSquare,
  PenLine, Play, Plus, RefreshCw, Repeat, Trash2,
} from 'lucide-react'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'
import { type ActionType, blankActionOfType } from './ActionListEditor'
import { FlowCanvas, FlowRows } from './flowKit'
import { type ActionPath, pathEquals, removeAtPath, resolveAtPath, updateAtPath } from './actionPath'

type Row = Record<string, unknown>
type Field = 'steps' | 'then_steps' | 'else_steps'
type Tone = 'blue' | 'green' | 'red' | 'orange' | 'purple' | 'yellow'

/** What a step kind looks like: its colour family and icon. */
const LOOK: Record<string, { tone: Tone; icon: ReactNode }> = {
  run_query: { tone: 'blue', icon: <Database size={15} /> },
  call_api: { tone: 'green', icon: <Globe size={15} /> },
  call_plugin: { tone: 'purple', icon: <Boxes size={15} /> },
  call_action: { tone: 'yellow', icon: <Link2 size={15} /> },
  if: { tone: 'orange', icon: <GitBranch size={15} /> },
  loop: { tone: 'orange', icon: <Repeat size={15} /> },
  chain: { tone: 'blue', icon: <LayoutList size={15} /> },
  return: { tone: 'green', icon: <Flag size={15} /> },
  set_field: { tone: 'green', icon: <PenLine size={15} /> },
  notify: { tone: 'yellow', icon: <MessageSquare size={15} /> },
  send_notification: { tone: 'yellow', icon: <Bell size={15} /> },
  confirm: { tone: 'yellow', icon: <MessageSquare size={15} /> },
  navigate: { tone: 'purple', icon: <CornerDownLeft size={15} /> },
  open_url: { tone: 'purple', icon: <ExternalLink size={15} /> },
  refresh: { tone: 'blue', icon: <RefreshCw size={15} /> },
}
/** The kinds a "+" offers, in the order a service is usually built — what only a screen does (navigate, confirm…) is left out. */
const OFFERED: ActionType[] = ['run_query', 'call_api', 'call_plugin', 'call_action', 'if', 'loop', 'notify', 'send_notification', 'return']

const Line = styled.div`display: flex; align-items: center; width: max-content;`
const Card = styled.button<{ $tone: Tone; $on: boolean }>`
  position: relative; flex: 0 0 auto; width: 200px; text-align: left; cursor: pointer; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 4px; font-family: ${fonts.sans};
  background: var(--bg-modal); border-radius: ${radius.lg};
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : colors.border)};
  box-shadow: ${(p) => (p.$on ? shadow.focus : 'none')};
  transition: border-color 0.15s, transform 0.15s;
  &:hover { border-color: ${(p) => (p.$on ? colors.blue.main : colors[p.$tone].border)}; transform: translateY(-1px); }
  & .kind { display: flex; align-items: center; gap: 6px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ${(p) => colors[p.$tone].main}; }
  & .chip { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: ${radius.md}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border}; }
  & .name { font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .what { font-size: ${fontSize.micro}; font-family: ${fonts.mono}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-height: 1.2em; }
  & .gone { position: absolute; top: 6px; right: 6px; display: none; color: ${colors.text.muted}; background: transparent; border: none; cursor: pointer; padding: 2px; }
  &:hover .gone { display: inline-flex; }
  & .gone:hover { color: ${colors.red.main}; }
`
const End = styled.button<{ $on?: boolean }>`
  flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 9px 14px; cursor: pointer; font-family: ${fonts.sans};
  border-radius: 999px; background: ${colors.blue.bg}; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-weight: 600;
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : colors.blue.border)};
  & small { font-weight: 400; color: ${colors.text.muted}; font-family: ${fonts.mono}; font-size: ${fontSize.micro}; }
`
const LinkWrap = styled.div`position: relative; flex: 0 0 auto; width: 44px; height: 26px; display: flex; align-items: center;`
const Wire = styled.div`
  width: 100%; height: 2px; background: ${colors.border}; position: relative;
  &::after { content: ''; position: absolute; right: 0; top: -3px; border-left: 6px solid ${colors.border}; border-top: 4px solid transparent; border-bottom: 4px solid transparent; }
`
const AddBtn = styled.button`
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 20px; height: 20px; padding: 0; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; border-radius: 999px;
  background: var(--bg-modal); color: ${colors.text.muted}; border: 1px solid ${colors.border};
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.main}; background: ${colors.blue.bg}; }
`
const Menu = styled.div`
  position: absolute; left: 50%; top: 26px; transform: translateX(-50%); z-index: 5; min-width: 190px; padding: 6px;
  background: var(--bg-modal); border: 1px solid ${colors.border}; border-radius: ${radius.md}; box-shadow: ${shadow.modal};
  display: flex; flex-direction: column; gap: 2px;
  & button { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: transparent; border: none; cursor: pointer; text-align: left;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; border-radius: ${radius.sm}; }
  & button:hover { background: var(--hover-subtle); }
`
const Fork = styled.div`display: flex; flex-direction: column; gap: 10px; padding: 4px 0 4px 14px; margin-left: 4px; border-left: 2px solid ${colors.border}; border-radius: ${radius.md};`
const Lane = styled.div`display: flex; align-items: center;`
const Pill = styled.span<{ $tone: Tone }>`
  flex: 0 0 auto; padding: 2px 9px; border-radius: 999px; font-size: ${fontSize.micro}; font-weight: 600;
  color: ${(p) => colors[p.$tone].main}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border};
`
const Frame = styled.div`
  flex: 0 0 auto; display: flex; flex-direction: column; gap: 8px; padding: 10px 12px 12px;
  border: 1px dashed ${colors.orange.border}; border-radius: ${radius.lg}; background: ${colors.orange.bg};
`

function text(v: unknown): string { return typeof v === 'string' ? v : v == null ? '' : String(v) }

/** What a step does, in one short line — its target, its condition, what it returns. */
export function stepSummary(a: Row): string {
  switch (a.type) {
    case 'run_query': return [text(a.connector), text(a.query)].filter(Boolean).join('.')
    case 'call_api': return [text(a.connector), text(a.endpoint)].filter(Boolean).join('.')
    case 'call_plugin': return text(a.callable)
    case 'call_action': return text(a.ref)
    case 'if': { const c = (a.condition ?? {}) as Row; return [text(c.source), text(c.operator), text(c.value)].filter(Boolean).join(' ') }
    case 'loop': return text(a.source)
    case 'return': return Object.keys((a.bindings ?? {}) as Row).join(', ')
    case 'set_field': return text(a.target)
    case 'notify': case 'confirm': return text(a.message)
    case 'send_notification': return text(a.rule)
    case 'navigate': return text(a.screen) || text(a.to)
    case 'open_url': return text(a.url)
    default: return ''
  }
}

/** *root* with *action* inserted at *index* of the list *field* of the step at *parent* (the root list when empty). */
export function insertStep(root: Row[], parent: ActionPath, field: Field | null, index: number, action: Row): Row[] {
  if (parent.length === 0 || field == null) { const next = root.slice(); next.splice(index, 0, action); return next }
  const owner = resolveAtPath(root, parent)
  if (!owner) return root
  const list = Array.isArray(owner[field]) ? (owner[field] as Row[]).slice() : []
  list.splice(index, 0, action)
  return updateAtPath(root, parent, { [field]: list })
}

/** An id no step of *root* has yet: ``<kind>_<n>``. */
export function freshStepId(root: Row[], kind: string): string {
  const taken = new Set<string>()
  const walk = (list: unknown) => { for (const a of Array.isArray(list) ? (list as Row[]) : []) { taken.add(text(a.id)); walk(a.steps); walk(a.then_steps); walk(a.else_steps) } }
  walk(root)
  const base = kind.replace(/^(run|call)_/, '')
  for (let n = 1; ; n++) if (!taken.has(`${base}_${n}`)) return `${base}_${n}`
}

export default function ActionFlow({ actions, inputs, onChange, path, onPathChange }: {
  actions: Row[]
  /** The names of the service's inputs, shown on the start. */
  inputs: string[]
  onChange: (next: Row[]) => void
  path: ActionPath | null
  onPathChange: (next: ActionPath | null) => void
}) {
  const { t } = useTranslation()
  const [adding, setAdding] = useState<string | null>(null)       // the link whose "+" menu is open
  useEffect(() => {
    if (!adding) return
    const close = () => setAdding(null)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [adding])
  const returned = useMemo(() => {
    const names: string[] = []
    const walk = (list: unknown) => { for (const a of Array.isArray(list) ? (list as Row[]) : []) { if (a.type === 'return') names.push(...Object.keys((a.bindings ?? {}) as Row)); walk(a.steps); walk(a.then_steps); walk(a.else_steps) } }
    walk(actions)
    return [...new Set(names)]
  }, [actions])

  const add = (parent: ActionPath, field: Field | null, index: number, kind: ActionType) => {
    const next = insertStep(actions, parent, field, index, blankActionOfType(kind, freshStepId(actions, kind)))
    setAdding(null)
    onChange(next)
    onPathChange(field == null ? [{ field: null, i: index }] : [...parent, { field, i: index }])
  }
  const remove = (at: ActionPath) => { onChange(removeAtPath(actions, at)); onPathChange(null) }
  const link = (parent: ActionPath, field: Field | null, index: number): ReactNode => {
    const key = `${JSON.stringify(parent)}/${field}/${index}`
    return (
      <LinkWrap key={`l-${key}`}>
        <Wire />
        <AddBtn type="button" title={t('settings.studio.addStep')} aria-label={t('settings.studio.addStep')}
          onMouseDown={(e) => e.stopPropagation()} onClick={() => setAdding(adding === key ? null : key)}><Plus size={12} /></AddBtn>
        {adding === key && (
          <Menu onMouseDown={(e) => e.stopPropagation()}>
            {OFFERED.map((kind) => (
              <button type="button" key={kind} onClick={() => add(parent, field, index, kind)}>
                <span style={{ color: colors[LOOK[kind].tone].main, display: 'inline-flex' }}>{LOOK[kind].icon}</span>{t(`settings.studio.kind.${kind}`)}
              </button>
            ))}
          </Menu>
        )}
      </LinkWrap>
    )
  }
  const card = (a: Row, at: ActionPath): ReactNode => {
    const kind = text(a.type)
    const look = LOOK[kind] ?? { tone: 'blue' as Tone, icon: <FileText size={15} /> }
    return (
      <Card type="button" $tone={look.tone} $on={pathEquals(path, at)} onClick={() => onPathChange(at)}>
        <span className="kind"><span className="chip">{look.icon}</span>{t(`settings.studio.kind.${kind}`, { defaultValue: kind })}</span>
        <span className="name">{text(a.label) || text(a.id)}</span>
        <span className="what">{stepSummary(a)}</span>
        <span className="gone" role="button" tabIndex={-1} title={t('common.delete')}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); remove(at) } }}
          onClick={(e) => { e.stopPropagation(); remove(at) }}><Trash2 size={13} /></span>
      </Card>
    )
  }
  // a list of steps: link, step, link, step… and a last link (where the next one goes)
  const sequence = (steps: Row[], parent: ActionPath, field: Field | null): ReactNode => (
    <Line>
      {steps.map((a, i) => {
        const at: ActionPath = field == null ? [{ field: null, i }] : [...parent, { field, i }]
        return <Line key={`${text(a.id)}-${i}`}>{link(parent, field, i)}{step(a, at)}</Line>
      })}
      {link(parent, field, steps.length)}
    </Line>
  )
  const list = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : [])
  const step = (a: Row, at: ActionPath): ReactNode => {
    if (a.type === 'if') {
      return (
        <Line>
          {card(a, at)}
          <Fork>
            <Lane><Pill $tone="green">{t('settings.studio.whenTrue')}</Pill>{sequence(list(a.then_steps), at, 'then_steps')}</Lane>
            <Lane><Pill $tone="red">{t('settings.studio.whenFalse')}</Pill>{sequence(list(a.else_steps), at, 'else_steps')}</Lane>
          </Fork>
        </Line>
      )
    }
    if (a.type === 'loop' || a.type === 'chain') {
      return <Frame>{card(a, at)}{sequence(list(a.steps), at, 'steps')}</Frame>
    }
    return card(a, at)
  }

  return (
    <FlowCanvas zoomKey="service">
      {/* the service's own steps wrap onto the next row; what is nested (a branch, a loop) stays in one piece */}
      <FlowRows>
        <End type="button" $on={!path || path.length === 0} onClick={() => onPathChange(null)}>
          <Play size={15} /> {t('settings.studio.start')}{inputs.length > 0 && <small>{inputs.join(', ')}</small>}
        </End>
        {actions.map((a, i) => <Line key={`${text(a.id)}-${i}`}>{link([], null, i)}{step(a, [{ field: null, i }])}</Line>)}
        <Line>
          {link([], null, actions.length)}
          <End type="button" as="div" style={{ cursor: 'default', background: colors.green.bg, borderColor: colors.green.border }}>
            <Flag size={15} /> {t('settings.studio.answer')}{returned.length > 0 && <small>{returned.join(', ')}</small>}
          </End>
        </Line>
      </FlowRows>
    </FlowCanvas>
  )
}
