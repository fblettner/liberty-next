// How the tests went: every suite with its last runs (how many passed, failed, changed; the time taken; who)
// and each case with its last results as a strip, when it last ran, how long it took and what was found.
// A case opens in the Tests page, where it is edited and played.
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { ArrowRight, CheckCircle2, CircleDashed, FlaskConical, XCircle } from 'lucide-react'
import { Banner, Button, Centered } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius, media } from '../../theme'

type Status = 'passed' | 'failed' | 'changed' | 'skipped'
interface RunLine { at: string; mode: string | null; who: string | null; passed: number; failed: number; changed: number; skipped: number; duration_ms: number }
interface CaseLine { id: string; label: string | null; writes: boolean; steps: number; history: string[]
  last: { status: string; at: string; mode: string | null; duration_ms: number; problems: string[] } | null }
interface SuiteLine { name: string; label?: string | null; connector?: string | null; error?: string; runs: RunLine[]; cases: CaseLine[] }
interface Went { suites: SuiteLine[]; totals: { cases: number; passed: number; failed: number; changed: number; skipped: number; never: number; last_at: string | null } }

const COLOR: Record<string, string> = { passed: colors.green.main, failed: colors.red.main, changed: colors.orange.main, skipped: colors.text.muted }
const Wrap = styled.div`display: flex; flex-direction: column; gap: 18px; max-width: 1500px;`
const Tiles = styled.div`display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; ${media.narrow} { grid-template-columns: repeat(3, minmax(0, 1fr)); }`
const Tile = styled.div<{ $color?: string }>`
  display: flex; flex-direction: column; gap: 2px; padding: 12px 14px; border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: var(--bg-modal);
  & b { font-size: 22px; font-weight: 600; color: ${(p) => p.$color ?? colors.text.primary}; font-variant-numeric: tabular-nums; }
  & span { font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.muted}; }
  & small { font-size: ${fontSize.sm}; color: ${colors.text.secondary}; }
`
const Suite = styled.div`display: flex; flex-direction: column; gap: 14px; padding: 16px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};`
const Head = styled.div`
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  & h3 { margin: 0; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; }
  & .mono { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.muted}; }
`
const Runs = styled.div`display: flex; align-items: flex-end; gap: 4px; height: 64px; padding: 0 2px;`
const RunBar = styled.div`
  flex: 0 0 16px; height: 100%; display: flex; flex-direction: column-reverse; border-radius: 3px; overflow: hidden; background: ${colors.bg.input};
  & i { display: block; width: 100%; }
`
const Table = styled.div`
  display: grid; grid-template-columns: 26px minmax(180px, 2fr) 150px 130px 90px minmax(160px, 3fr); align-items: center; row-gap: 2px; column-gap: 12px;
  & .h { font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.muted}; padding-bottom: 4px; }
  ${media.tablet} { grid-template-columns: 26px minmax(0, 1fr) 110px; & .wide { display: none; } }
`
const Row = styled.button`
  display: contents; cursor: pointer; font-family: ${fonts.sans}; text-align: left;
  & > * { padding: 0; min-height: 36px; display: flex; align-items: center; border-top: 1px solid ${colors.border}; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; min-width: 0; }
  &:hover > * { background: var(--hover-subtle); }
  & .name { color: ${colors.text.primary}; font-weight: 600; white-space: nowrap; overflow: hidden; }
  & .name small { font-weight: 400; font-family: ${fonts.mono}; color: ${colors.text.muted}; margin-left: 8px; }
  & .found { color: ${colors.red.main}; font-family: ${fonts.mono}; font-size: ${fontSize.micro}; overflow: hidden; white-space: nowrap; }
  & .num { font-variant-numeric: tabular-nums; }
`
const Strip = styled.span`display: inline-flex; gap: 3px; align-items: center; & i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }`

function icon(status: string | undefined): ReactNode {
  if (status === 'passed') return <CheckCircle2 size={15} color={colors.green.main} />
  if (status === 'failed') return <XCircle size={15} color={colors.red.main} />
  if (status === 'changed') return <XCircle size={15} color={colors.orange.main} />
  return <CircleDashed size={15} color={colors.text.muted} />
}
const when = (iso: string | null | undefined): string => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
const took = (ms: number): string => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`)

export default function TestResults() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [data, setData] = useState<Went | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.get<Went>('/admin/test-results').then(setData).catch((e) => setError(e instanceof ApiError ? e.message : String(e))) }, [])
  if (error) return <Banner $tone="error">{error}</Banner>
  if (!data) return <Centered />
  const tt = data.totals
  const edit = (suite: string, id?: string) => navigate(`/liberty/tests?suite=${encodeURIComponent(suite)}${id ? `&case=${encodeURIComponent(id)}` : ''}`)
  return (
    <Wrap>
      <Tiles>
        <Tile><span>{t('testResults.cases')}</span><b>{tt.cases}</b></Tile>
        <Tile $color={colors.green.main}><span>{t('testResults.passed')}</span><b>{tt.passed}</b></Tile>
        <Tile $color={tt.failed ? colors.red.main : undefined}><span>{t('testResults.failed')}</span><b>{tt.failed}</b></Tile>
        <Tile $color={tt.changed ? colors.orange.main : undefined}><span>{t('testResults.changed')}</span><b>{tt.changed}</b></Tile>
        <Tile><span>{t('testResults.notRun')}</span><b>{tt.never + tt.skipped}</b></Tile>
        <Tile><span>{t('testResults.lastRun')}</span><small style={{ marginTop: 6 }}>{when(tt.last_at)}</small></Tile>
      </Tiles>
      {data.suites.length === 0 && <Banner $tone="info">{t('tests.none')}</Banner>}
      {data.suites.map((s) => {
        const last = s.runs[0]
        const shown = s.runs.slice(0, 20).reverse()                    // the oldest on the left, the latest on the right
        return (
          <Suite key={s.name}>
            <Head>
              <FlaskConical size={17} color={colors.blue.main} />
              <h3>{s.label || s.name}</h3>
              <span className="mono">{s.name}{s.connector ? ` · ${s.connector}` : ''}</span>
              <span style={{ flex: 1 }} />
              {last && <span className="mono">{t('testResults.lastLine', { when: when(last.at), who: last.who ?? '—', took: took(last.duration_ms) })}</span>}
              <Button $variant="ghost" $size="sm" onClick={() => edit(s.name)}>{t('testResults.open')} <ArrowRight size={13} /></Button>
            </Head>
            {s.error && <Banner $tone="error">{s.error}</Banner>}
            {shown.length > 0 && (
              <Runs title={t('testResults.runsHint')}>
                {shown.map((r, i) => {
                  const total = Math.max(1, r.passed + r.failed + r.changed + r.skipped)
                  return (
                    <RunBar key={i} title={`${when(r.at)} · ${r.mode ?? ''} · ${r.passed} ✓ ${r.failed} ✗ ${r.changed} ≠ ${r.skipped} – · ${took(r.duration_ms)}`}>
                      {(['passed', 'changed', 'failed', 'skipped'] as Status[]).map((k) => (r[k] ? <i key={k} style={{ height: `${(r[k] / total) * 100}%`, background: COLOR[k] }} /> : null))}
                    </RunBar>
                  )
                })}
              </Runs>
            )}
            <Table>
              <span className="h" /><span className="h">{t('testResults.case')}</span><span className="h wide">{t('testResults.history')}</span>
              <span className="h">{t('testResults.lastRun')}</span><span className="h wide">{t('testResults.duration')}</span><span className="h wide">{t('tests.found')}</span>
              {s.cases.map((c) => (
                <Row key={c.id} type="button" onClick={() => edit(s.name, c.id)}>
                  <span>{icon(c.last?.status)}</span>
                  <span className="name">{c.label || c.id}{c.label && <small>{c.id}</small>}</span>
                  <span className="wide"><Strip>{[...c.history].reverse().map((h, i) => <i key={i} title={h} style={{ background: COLOR[h] ?? colors.text.muted }} />)}</Strip></span>
                  <span className="num">{c.last ? when(c.last.at) : t('testResults.never')}</span>
                  <span className="num wide">{c.last ? took(c.last.duration_ms) : ''}</span>
                  <span className="found wide" title={(c.last?.problems ?? []).join('\n')}>{c.last?.problems[0] ?? ''}</span>
                </Row>
              ))}
            </Table>
          </Suite>
        )
      })}
    </Wrap>
  )
}
