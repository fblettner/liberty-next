// nomaflow Job Runs — the run history across every job: search, job, period and state filters
// (the state chips carry their counts), the runs grouped by day, one line each (state, time,
// job with the run's parameters — the application of a for_each run —, trigger, duration, rows),
// the error under a failed run, a progress bar while a run is going. Live while anything runs.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Activity, Clock, GitBranch, RefreshCw, Search, User } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Tag, Input, SearchSelect, type SearchSelectOption } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { JobsListResponse, RunState } from './types'
import { STATE_TONE } from './util'
import { formatDuration, groupByDay, paramLabels, runSeconds, type RunRow } from './runsView'

const PAGE = 100
const POLL_MS = 4000
const FILTER_KEY = 'liberty.nomaflow.runs.filters'
const STATES: RunState[] = ['RUNNING', 'QUEUED', 'FAILED', 'SUCCEEDED', 'CANCELED', 'MISSED']

const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1;`
const Chips = styled.div`display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px;`
const Chip = styled.button<{ $on: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 999px; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  border: 1px solid ${({ $on }) => ($on ? colors.blue.border : colors.border)};
  background: ${({ $on }) => ($on ? colors.blue.bg : colors.bg.card)};
  color: ${({ $on }) => ($on ? colors.text.primary : colors.text.secondary)};
  & b { font-weight: 600; color: ${colors.text.primary}; }
`
const Group = styled.div`flex: none; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; overflow: hidden; margin-bottom: 10px;`
const DayHead = styled.div`
  display: flex; justify-content: space-between; padding: 7px 12px; background: ${colors.bg.input};
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary};
  & span { font-weight: 400; color: ${colors.text.muted}; }
`
const Row = styled.div`
  padding: 8px 12px; border-top: 1px solid ${colors.border}; cursor: pointer; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:hover { background: var(--hover-subtle); }
  & .main { display: grid; grid-template-columns: 104px 76px minmax(200px, 1.6fr) minmax(120px, 1fr) 130px 80px; align-items: center; gap: 12px; }
  & .time { font-family: ${fonts.mono}; color: ${colors.text.primary}; white-space: nowrap; }
  & .job { display: flex; align-items: center; gap: 6px; min-width: 0; }
  & .job .id { font-family: ${fonts.mono}; font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .trig { display: flex; align-items: center; gap: 5px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  & .rows { text-align: right; font-family: ${fonts.mono}; }
  & .err { margin: 4px 0 0 204px; color: ${colors.red.main}; font-size: ${fontSize.micro}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @media (max-width: 1000px) {
    & .main { grid-template-columns: 96px 72px minmax(0, 1fr) 90px; }
    & .trig, & .rows { display: none; }
    & .err { margin-left: 0; }
  }
`
const Param = styled.span`
  flex: none; padding: 0 7px; border-radius: 999px; font-size: ${fontSize.micro}; white-space: nowrap;
  background: ${colors.blue.bg}; border: 1px solid ${colors.blue.border}; color: ${colors.text.secondary};
`
const Bar = styled.span`
  display: block; height: 4px; margin-top: 3px; border-radius: 2px; background: ${colors.bg.input}; overflow: hidden;
  & i { display: block; height: 100%; background: ${colors.blue.main}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 18px 2px;`

interface RunsResponse { runs: RunRow[]; counts: Partial<Record<RunState, number>>; total: number }

function readFilters(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(FILTER_KEY) ?? '{}') as Record<string, string> } catch { return {} }
}

export default function RunsList() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const saved = useMemo(readFilters, [])
  const [q, setQ] = useState(saved.q ?? '')
  const [job, setJob] = useState(saved.job ?? '')
  const [hours, setHours] = useState(saved.hours ?? '168')
  const [state, setState] = useState(saved.state ?? '')
  const [data, setData] = useState<RunsResponse | null>(null)
  const [limit, setLimit] = useState(PAGE)
  const [jobIds, setJobIds] = useState<SearchSelectOption[]>([])
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const seq = useRef(0)

  useEffect(() => { try { localStorage.setItem(FILTER_KEY, JSON.stringify({ q, job, hours, state })) } catch { /* ignore */ } }, [q, job, hours, state])
  useEffect(() => {
    api.get<JobsListResponse>('/admin/jobs')
      .then((r) => setJobIds(r.jobs.map((j) => ({ value: j.id, label: j.id, mono: j.id }))))
      .catch(() => setJobIds([]))
  }, [])

  const load = useCallback(() => {
    const mine = ++seq.current
    const p = new URLSearchParams({ limit: String(limit) })
    if (q.trim()) p.set('q', q.trim())
    if (job) p.set('job', job)
    if (hours) p.set('hours', hours)
    if (state) p.set('state', state)
    api.get<RunsResponse>(`/admin/jobs/runs?${p.toString()}`)
      .then((r) => { if (mine === seq.current) { setData(r); setError(null); setNow(Date.now()) } })
      .catch((e) => { if (mine === seq.current) setError(e instanceof ApiError ? (e.status === 403 ? t('nomaflow.superuserRequired') : e.message) : String(e)) })
  }, [q, job, hours, state, limit, t])
  // typing in the search waits a moment; the other filters reload at once
  useEffect(() => { const h = setTimeout(load, 250); return () => clearTimeout(h) }, [load])
  const live = !!data?.runs.some((r) => r.state === 'RUNNING' || r.state === 'QUEUED') || !!(data?.counts.RUNNING || data?.counts.QUEUED)
  useEffect(() => {
    if (!live) return
    const h = setInterval(load, POLL_MS)
    return () => clearInterval(h)
  }, [live, load])

  const days = useMemo(() => groupByDay(data?.runs ?? [], new Date(now)), [data, now])
  const dayLabel = (d: { date: Date; offset: number }) => d.offset === 0 ? t('nomaflow.scheduleView.today')
    : d.offset === 1 ? t('nomaflow.runsView.yesterday')
    : d.date.toLocaleDateString(i18n.language, { weekday: 'long', day: 'numeric', month: 'long' })
  const total = Object.values(data?.counts ?? {}).reduce((a, b) => a + (b ?? 0), 0)
  const periods: SearchSelectOption[] = [
    { value: '24', label: t('nomaflow.runsView.last24h') },
    { value: '168', label: t('nomaflow.runsView.last7d') },
    { value: '720', label: t('nomaflow.runsView.last30d') },
    { value: '', label: t('nomaflow.runsView.allTime') },
  ]
  const trigger = (r: RunRow) => r.trigger_kind === 'scheduled'
    ? <><Clock size={13} /> {t('nomaflow.runsView.scheduled')}</>
    : (r.triggered_by ?? '').startsWith('call_job:')
      ? <><GitBranch size={13} /> {r.triggered_by!.slice('call_job:'.length).split('@')[0]}</>
      : <><User size={13} /> {r.triggered_by ?? t('nomaflow.runsView.manual')}</>

  return (
    <PageLayout icon={<Activity size={18} />} title={t('nomaflow.runsView.title')} description={t('nomaflow.runsView.subtitle')}>
      <Toolbar>
        <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 340 }}>
          <Search size={14} style={{ position: 'absolute', left: 9, top: 9, color: colors.text.muted }} />
          <Input value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE) }} placeholder={t('nomaflow.runsView.search')} style={{ paddingLeft: 28 }} />
        </div>
        <div style={{ width: 240 }}>
          <SearchSelect sort value={job} onChange={(v) => { setJob(v); setLimit(PAGE) }} options={jobIds} anyLabel={t('nomaflow.runsView.allJobs')} placeholder={t('nomaflow.runsView.allJobs')} />
        </div>
        <div style={{ width: 170 }}>
          <SearchSelect value={hours} onChange={(v) => { setHours(v); setLimit(PAGE) }} options={periods} />
        </div>
        <Spacer />
        {live && <Tag $tone="blue">{t('nomaflow.run.live')}</Tag>}
        <Button $variant="ghost" $size="sm" onClick={load}><RefreshCw size={14} /> {t('common.reload')}</Button>
      </Toolbar>

      <Chips>
        <Chip type="button" $on={!state} onClick={() => { setState(''); setLimit(PAGE) }}>{t('common.all')} <b>{total}</b></Chip>
        {STATES.filter((s) => (data?.counts[s] ?? 0) > 0 || s === state).map((s) => (
          <Chip key={s} type="button" $on={state === s} onClick={() => { setState(state === s ? '' : s); setLimit(PAGE) }}>
            <Tag $tone={STATE_TONE[s]}>{s}</Tag> <b>{data?.counts[s] ?? 0}</b>
          </Chip>
        ))}
      </Chips>

      {error && <Banner $tone="error">{error}</Banner>}
      {!data && !error && <Centered />}
      {data && data.runs.length === 0 && <Empty>{t('nomaflow.runsView.none')}</Empty>}

      {days.map((d) => (
        <Group key={d.key}>
          <DayHead>{dayLabel(d)} <span>{t('nomaflow.runsView.count', { count: d.runs.length })}</span></DayHead>
          {d.runs.map((r) => {
            const iso = r.started_at ?? r.scheduled_at
            const secs = runSeconds(r, now)
            const params = paramLabels(r.params)
            return (
              <Row key={r.id} onClick={() => navigate(`/nomaflow/runs/${encodeURIComponent(r.id)}`)} title={iso ? new Date(iso).toLocaleString() : undefined}>
                <div className="main">
                  <span><Tag $tone={STATE_TONE[r.state]}>{r.state}</Tag></span>
                  <span className="time">{iso ? new Date(iso).toLocaleTimeString(i18n.language, { hour: '2-digit', minute: '2-digit' }) : '—'}</span>
                  <span className="job">
                    <span className="id">{r.job_id}</span>
                    {params.map((p) => <Param key={p}>{p}</Param>)}
                  </span>
                  <span className="trig">{trigger(r)}</span>
                  <span>
                    {r.state === 'MISSED' ? '—' : formatDuration(secs)}
                    {r.progress && <Bar title={`${r.progress.done} / ${r.progress.total}`}><i style={{ width: `${Math.min(100, (100 * r.progress.done) / r.progress.total)}%` }} /></Bar>}
                  </span>
                  <span className="rows">{r.rows_affected ?? '—'}</span>
                </div>
                {r.state === 'FAILED' && r.error_message && <div className="err" title={r.error_message}>{r.error_message}</div>}
              </Row>
            )
          })}
        </Group>
      ))}

      {data && data.runs.length < data.total && (
        <Button $variant="ghost" onClick={() => setLimit((l) => l + PAGE)}>{t('nomaflow.runsView.more', { n: data.total - data.runs.length })}</Button>
      )}
    </PageLayout>
  )
}
