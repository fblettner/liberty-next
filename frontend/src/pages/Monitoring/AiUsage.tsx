// AI usage (Monitoring): the assistant's audit and usage record over a period — totals with an
// estimated cost, per user / day / model / channel / tool, what was changed through the
// assistant, and the latest activity with its tool calls. What is recorded: who, model, tokens,
// tools; never the question, the answer or a row.
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, RefreshCw, X } from 'lucide-react'
import { ApiError } from '../../api/client'
import { Banner, Button, Centered, Checkbox, SearchSelect, type SearchSelectOption } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import {
  average, cachedShare, formatCost, formatDuration, formatTokens, getUsage, groupActivity, tokensIn,
  type Turn, type TurnTool, type UsageFigures, type UsageResponse,
} from '../../services/aiUsage'

// The page's content scrolls in the page frame, a flex column: without this the panels below
// (overflow: hidden, so no minimum height) were squeezed to fit — the tools list cut, the latest
// questions pushed out of sight — instead of the frame scrolling.
const Root = styled.div`flex-shrink: 0;`
const Bar = styled.div`display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1;`
const Note = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro};`
const Tiles = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px;`
const Tile = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; padding: 10px 14px;
  & .v { color: ${colors.text.primary}; font-size: ${fontSize.xl}; font-weight: 600; }
  & .l { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
// Two panels a row (one on a narrow window): six panels fill three rows, none leaves half a row empty.
const Grid = styled.div`
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-bottom: 14px;
  @media (max-width: 1100px) { grid-template-columns: minmax(0, 1fr); }
`
const Panel = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; overflow: hidden;`
const Title = styled.div`
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 8px 14px; color: ${colors.text.primary}; font-weight: 600; font-size: ${fontSize.sm}; border-bottom: 1px solid ${colors.border};
  & .sub { font-weight: 400; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const Scroll = styled.div`max-height: 300px; overflow: auto;`
const T = styled.table`
  width: 100%; border-collapse: collapse; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & th { text-align: left; font-weight: 500; color: ${colors.text.muted}; font-size: ${fontSize.micro}; padding: 6px 14px; position: sticky; top: 0; background: ${colors.bg.dropdown}; }
  & td { padding: 6px 14px; border-top: 1px solid ${colors.border}; vertical-align: top; }
  & .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  & .name { color: ${colors.text.primary}; }
  & .mono { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; }
  & .bad { color: ${colors.red.main}; }
  & .wrap { word-break: break-word; }
  & tr.pick { cursor: pointer; }
  & tr.on td { background: ${colors.bg.input}; }
`
const Empty = styled.div`padding: 14px; color: ${colors.text.muted}; font-size: ${fontSize.sm};`

const PERIODS = [7, 30, 90]
const CHANNELS = ['chat', 'mcp', 'apply']

export default function AiUsage() {
  const { t, i18n } = useTranslation()
  const [days, setDays] = useState(30)
  const [user, setUser] = useState<string | null>(null)
  const [channel, setChannel] = useState('')
  const [failedOnly, setFailedOnly] = useState(false)
  const [data, setData] = useState<UsageResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const load = useCallback(() => {
    setBusy(true)
    getUsage(days, user, channel || null, failedOnly)
      .then((r) => { setData(r); setError(null) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setBusy(false))
  }, [days, user, channel, failedOnly])
  useEffect(load, [load])

  const channelLabel = useCallback((c: string) => (c === 'mcp' ? t('aiUsage.channelMcp') : c === 'apply' ? t('aiUsage.channelApply') : t('aiUsage.channelChat')), [t])
  const periodOpts = useMemo<SearchSelectOption[]>(() => PERIODS.map((d) => ({ value: String(d), label: t('aiUsage.lastDays', { days: d }) })), [t])
  const channelOpts = useMemo<SearchSelectOption[]>(() => CHANNELS.map((c) => ({ value: c, label: channelLabel(c) })), [channelLabel])
  const when = (ts: string | null) => (ts ? new Date(ts).toLocaleString(i18n.language, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '')
  const activity = useMemo(() => groupActivity(data?.recent ?? []), [data])

  if (!data && !error) return <Centered />
  const s = data?.summary
  const figures = (f: UsageFigures) => (
    <>
      <td className="num">{f.turns}</td>
      <td className="num">{formatTokens(tokensIn(f))}</td>
      <td className="num">{cachedShare(f)} %</td>
      <td className="num">{formatTokens(f.output_tokens)}</td>
      <td className="num">{formatCost(f.cost)}</td>
    </>
  )
  const figureHead = (
    <>
      <th className="num">{t('aiUsage.turns')}</th>
      <th className="num">{t('aiUsage.tokensIn')}</th>
      <th className="num">{t('aiUsage.cached')}</th>
      <th className="num">{t('aiUsage.tokensOut')}</th>
      <th className="num">{t('aiUsage.cost')}</th>
    </>
  )
  const outcome = (turn: Turn) => (turn.error ? t('aiUsage.failed') : turn.stop_reason === 'abandoned' ? t('aiUsage.abandoned') : turn.stop_reason === 'end_turn' ? t('aiUsage.answered') : turn.stop_reason === 'tool_cap' ? t('aiUsage.toolCap') : turn.stop_reason ?? '')
  const toolLine = (c: TurnTool, key: number | string) => (
    <div key={key} className="mono wrap">
      <span className={c.ok === false ? 'bad' : 'name'}>{c.name}</span> {c.input}
      {c.ms !== undefined && ` · ${formatDuration(c.ms)}`}{c.chars !== undefined && ` · ${t('aiUsage.chars', { n: c.chars })}`}
      {c.ok === false && c.error ? <span className="bad"> — {c.error}</span> : null}
    </div>
  )
  const changes = s?.changes ?? []

  return (
    <Root>
      <Bar>
        <div style={{ width: 170 }}><SearchSelect value={String(days)} onChange={(v) => setDays(Number(v) || 30)} options={periodOpts} /></div>
        {user && <Button $variant="ghost" $size="sm" onClick={() => setUser(null)}><X size={13} /> {t('aiUsage.turnsOf', { user })}</Button>}
        <Note>{t('aiUsage.kept')} {data && (data.retention_days > 0 ? t('aiUsage.keptDays', { days: data.retention_days }) : t('aiUsage.keptAll'))}</Note>
        <Spacer />
        <Button $variant="ghost" $size="sm" onClick={load} disabled={busy}><RefreshCw size={14} /> {t('common.reload')}</Button>
      </Bar>
      {error && <Banner $tone="error">{error}</Banner>}
      {s && (
        <>
          <Tiles>
            <Tile><div className="v">{s.totals.turns}</div><div className="l">{t('aiUsage.turns')}</div></Tile>
            <Tile><div className="v">{s.by_user.length}</div><div className="l">{t('aiUsage.users')}</div></Tile>
            <Tile>
              <div className="v">{formatCost(s.totals.cost)}</div>
              <div className="l">{t('aiUsage.costNote')}{s.unpriced && s.unpriced.length > 0 ? ` · ${t('aiUsage.unpriced', { models: s.unpriced.join(', ') })}` : ''}</div>
            </Tile>
            <Tile><div className="v">{formatTokens(tokensIn(s.totals))}</div><div className="l">{t('aiUsage.tokensIn')} · {t('aiUsage.cachedShare', { pct: cachedShare(s.totals) })}</div></Tile>
            <Tile><div className="v">{formatTokens(s.totals.output_tokens)}</div><div className="l">{t('aiUsage.tokensOut')}</div></Tile>
            <Tile><div className="v">{s.totals.tool_calls}</div><div className="l">{t('aiUsage.toolCalls')} · {t('aiUsage.failedCount', { n: s.totals.tool_errors })}</div></Tile>
            <Tile><div className="v">{changes.length}</div><div className="l">{t('aiUsage.changes')}</div></Tile>
          </Tiles>
          {s.totals.turns === 0 ? <Panel><Empty>{t('aiUsage.none')}</Empty></Panel> : (
            <>
              <Grid>
                <Panel>
                  <Title>{t('aiUsage.byUser')}</Title>
                  <Scroll><T>
                    <thead><tr><th>{t('aiUsage.user')}</th>{figureHead}</tr></thead>
                    <tbody>{s.by_user.map((u) => (
                      <tr key={u.username} className={`pick${user === u.username ? ' on' : ''}`} onClick={() => setUser(user === u.username ? null : u.username)}>
                        <td className="name">{u.username}</td>{figures(u)}
                      </tr>
                    ))}</tbody>
                  </T></Scroll>
                </Panel>
                <Panel>
                  <Title>{t('aiUsage.byDay')}</Title>
                  <Scroll><T>
                    <thead><tr><th>{t('aiUsage.day')}</th>{figureHead}</tr></thead>
                    <tbody>{[...s.by_day].reverse().map((d) => (<tr key={d.day}><td className="name">{d.day}</td>{figures(d)}</tr>))}</tbody>
                  </T></Scroll>
                </Panel>
                <Panel>
                  <Title>{t('aiUsage.byModel')}<span className="sub">{t('aiUsage.byModelNote')}</span></Title>
                  <Scroll><T>
                    <thead><tr><th>{t('aiUsage.model')}</th>{figureHead}</tr></thead>
                    <tbody>{s.by_model.map((m) => (<tr key={m.model}><td className="name mono">{m.model}</td>{figures(m)}</tr>))}</tbody>
                  </T></Scroll>
                </Panel>
                <Panel>
                  <Title>{t('aiUsage.byChannel')}</Title>
                  <Scroll><T>
                    <thead><tr>
                      <th>{t('aiUsage.channel')}</th><th className="num">{t('aiUsage.entries')}</th><th className="num">{t('aiUsage.toolCalls')}</th>
                      <th className="num">{t('aiUsage.failedCol')}</th><th className="num">{t('aiUsage.cost')}</th>
                    </tr></thead>
                    <tbody>{(s.by_channel ?? []).map((c) => (
                      <tr key={c.channel} className={`pick${channel === c.channel ? ' on' : ''}`} onClick={() => setChannel(channel === c.channel ? '' : c.channel)}>
                        <td className="name">{channelLabel(c.channel)}</td>
                        <td className="num">{c.turns}</td><td className="num">{c.tool_calls}</td>
                        <td className={`num${c.tool_errors + c.errors ? ' bad' : ''}`}>{c.tool_errors + c.errors}</td>
                        <td className="num">{c.channel === 'chat' ? formatCost(c.cost) : '—'}</td>
                      </tr>
                    ))}</tbody>
                  </T></Scroll>
                </Panel>
                <Panel>
                  <Title>{t('aiUsage.byTool')}</Title>
                  <Scroll><T>
                    <thead><tr><th>{t('aiUsage.tool')}</th><th className="num">{t('aiUsage.calls')}</th><th className="num">{t('aiUsage.failedCol')}</th><th className="num">{t('aiUsage.avgTime')}</th><th className="num">{t('aiUsage.avgSize')}</th></tr></thead>
                    <tbody>{s.by_tool.map((tool) => (
                      <tr key={tool.name}>
                        <td className="name mono">{tool.name}</td>
                        <td className="num">{tool.calls}</td>
                        <td className={`num${tool.errors ? ' bad' : ''}`}>{tool.errors}</td>
                        <td className="num">{formatDuration(average(tool.ms, tool.calls))}</td>
                        <td className="num">{formatTokens(Math.round(average(tool.chars, tool.calls)))}</td>
                      </tr>
                    ))}</tbody>
                  </T></Scroll>
                </Panel>
                <Panel>
                  <Title>{t('aiUsage.changes')}<span className="sub">{t('aiUsage.changesNote')}</span></Title>
                  {changes.length === 0 ? <Empty>{t('aiUsage.changesNone')}</Empty> : (
                    <Scroll><T>
                      <thead><tr><th>{t('aiUsage.when')}</th><th>{t('aiUsage.user')}</th><th>{t('aiUsage.channel')}</th><th>{t('aiUsage.what')}</th></tr></thead>
                      <tbody>{changes.map((c, i) => (
                        <tr key={i}>
                          <td>{when(c.at)}</td>
                          <td className="name">{c.username}</td>
                          <td>{channelLabel(c.channel)}</td>
                          <td className="mono wrap">
                            <span className={c.ok === false ? 'bad' : 'name'}>{c.tool}</span> {c.what}
                            {c.ok === false && c.error ? <span className="bad"> — {c.error}</span> : null}
                          </td>
                        </tr>
                      ))}</tbody>
                    </T></Scroll>
                  )}
                </Panel>
              </Grid>
              <Panel>
                <Title>
                  {user ? t('aiUsage.turnsOf', { user }) : t('aiUsage.recent')}
                  <Spacer />
                  <div style={{ width: 190, fontWeight: 400 }}><SearchSelect value={channel} onChange={setChannel} options={channelOpts} anyLabel={t('aiUsage.anyChannel')} placeholder={t('aiUsage.anyChannel')} /></div>
                  <span style={{ fontWeight: 400 }}><Checkbox checked={failedOnly} onChange={setFailedOnly} label={t('aiUsage.failedOnly')} /></span>
                </Title>
                {activity.length === 0 ? <Empty>{t('aiUsage.noneMatching')}</Empty> : (
                  <T>
                    <thead><tr>
                      <th />
                      <th>{t('aiUsage.when')}</th><th>{t('aiUsage.user')}</th><th>{t('aiUsage.app')}</th><th>{t('aiUsage.model')}</th>
                      <th className="num">{t('aiUsage.tokensIn')}</th><th className="num">{t('aiUsage.cached')}</th><th className="num">{t('aiUsage.tokensOut')}</th>
                      <th className="num">{t('aiUsage.toolCalls')}</th><th className="num">{t('aiUsage.time')}</th><th>{t('aiUsage.outcome')}</th>
                    </tr></thead>
                    <tbody>{activity.map((a) => {
                      if (a.kind === 'mcp') {
                        // a run of MCP tool calls, one line: the calls are its detail
                        const first = a.turns[0]
                        const errors = a.turns.reduce((n, x) => n + x.tool_errors, 0)
                        return (
                          <Fragment key={a.id}>
                            <tr className="pick" onClick={() => setOpen(open === a.id ? null : a.id)}>
                              <td>{open === a.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</td>
                              <td>{when(first.at)}</td>
                              <td className="name">{first.username}</td>
                              <td />
                              <td>{channelLabel('mcp')}</td>
                              <td className="num">—</td><td className="num">—</td><td className="num">—</td>
                              <td className={`num${errors ? ' bad' : ''}`}>{a.turns.length}</td>
                              <td className="num">{formatDuration(a.turns.reduce((n, x) => n + x.duration_ms, 0))}</td>
                              <td className={errors ? 'bad' : undefined}>{errors ? t('aiUsage.failedCount', { n: errors }) : t('aiUsage.answered')}</td>
                            </tr>
                            {open === a.id && (
                              <tr className="on"><td /><td colSpan={10}>
                                {[...a.turns].reverse().flatMap((x) => x.tools.map((c, i) => toolLine(c, `${x.id}-${i}`)))}
                              </td></tr>
                            )}
                          </Fragment>
                        )
                      }
                      const turn = a.turn
                      const applied = turn.channel === 'apply'
                      return (
                        <Fragment key={turn.id}>
                          <tr className={turn.tools.length || turn.error ? 'pick' : undefined} onClick={() => (turn.tools.length || turn.error) && setOpen(open === turn.id ? null : turn.id)}>
                            <td>{turn.tools.length || turn.error ? (open === turn.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : null}</td>
                            <td>{when(turn.at)}</td>
                            <td className="name">{turn.username}</td>
                            <td>{turn.app ?? ''}</td>
                            <td className={applied ? undefined : 'mono'}>{applied ? channelLabel('apply') : turn.model}</td>
                            <td className="num">{applied ? '—' : formatTokens(tokensIn(turn))}</td>
                            <td className="num">{applied ? '—' : `${cachedShare(turn)} %`}</td>
                            <td className="num">{applied ? '—' : formatTokens(turn.output_tokens)}</td>
                            <td className={`num${turn.tool_errors ? ' bad' : ''}`}>{turn.tool_calls}</td>
                            <td className="num">{formatDuration(turn.duration_ms)}</td>
                            <td className={turn.error || (applied && turn.tool_errors) ? 'bad' : undefined}>
                              {applied ? (turn.tool_errors ? t('aiUsage.refused') : t('aiUsage.applied')) : outcome(turn)}
                            </td>
                          </tr>
                          {open === turn.id && (
                            <tr className="on"><td /><td colSpan={10}>
                              {turn.error && <div className="bad">{turn.error}</div>}
                              {turn.tools.map((c, i) => toolLine(c, i))}
                            </td></tr>
                          )}
                        </Fragment>
                      )
                    })}</tbody>
                  </T>
                )}
              </Panel>
            </>
          )}
        </>
      )}
    </Root>
  )
}
