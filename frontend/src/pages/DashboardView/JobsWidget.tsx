// The nomaflow jobs with their controls (a `jobs` widget — superusers only, the server drops it
// for anyone else): per job the next fire or "paused", the schedule in words, the last run's
// state, and Run now / Pause / Resume. Reads GET /admin/jobs; Run fires with the saved
// parameters; Pause / Resume flips the job's own schedule (POST /admin/jobs/{id}/enabled).
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Pause, Play } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, SpinnerRing } from '../../common'
import { describeCron } from '../../services/cronText'
import type { JobsWidgetWire } from '../../types/dashboards'
import type { Tone } from '../../services/widgets'
import type { JobsListResponse, JobSummary } from '../Nomaflow/types'
import { relative } from '../Nomaflow/util'
import { colors, radius } from '../../theme'
import { Card, Dot, Empty, Line, Main, Muted, Pill, Scroll, Sub, Title } from './cardParts'
import { pickJobs } from './jobsPick'

const Btn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; cursor: pointer;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input}; color: ${colors.text.secondary};
  &:hover:not(:disabled) { color: ${colors.blue.main}; border-color: ${colors.blue.border}; }
  &:disabled { opacity: 0.45; cursor: default; }
`
const Id = styled.span`cursor: pointer; &:hover { color: ${colors.blue.main}; }`

const TONE: Record<string, Tone> = { SUCCEEDED: 'ok', FAILED: 'error', CANCELED: 'warn', MISSED: 'warn', RUNNING: 'info', QUEUED: 'info' }

export function JobsWidget({ widget, refreshKey = 0 }: { widget: JobsWidgetWire; refreshKey?: number }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<JobSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(() => {
    api.get<JobsListResponse>('/admin/jobs')
      .then((r) => { setJobs(r.jobs); setError(null) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  useEffect(() => { load() }, [load, refreshKey])
  // while a run is going, follow it
  const live = !!jobs?.some((j) => j.in_flight)
  useEffect(() => {
    if (!live) return
    const h = setInterval(load, 5000)
    return () => clearInterval(h)
  }, [live, load])

  const act = async (job: JobSummary, what: 'run' | 'pause' | 'resume') => {
    setBusy(job.id); setError(null)
    try {
      if (what === 'run') await api.post(`/admin/jobs/${encodeURIComponent(job.id)}/run`, {})
      else await api.post(`/admin/jobs/${encodeURIComponent(job.id)}/enabled`, { enabled: what === 'resume' })
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(null) }
  }

  const rows = jobs ? pickJobs(jobs, widget) : []
  const when = (j: JobSummary) => {
    const cron = j.schedule ? describeCron(j.schedule, i18n.language) : null
    const sched = j.schedule ? (cron ? t(cron.key, cron.values) : j.schedule) : t('nomaflow.jobs.manualOnly')
    if (j.schedule && !j.enabled) return `${t('dashboard.jobs.paused')} · ${sched}`
    return j.next_run ? `${t('nomaflow.jobs.nextRun')} ${relative(j.next_run)} · ${sched}` : sched
  }
  return (
    <Card>
      <div>
        {widget.label && <Title>{widget.label}</Title>}
        {widget.description && <Sub>{widget.description}</Sub>}
      </div>
      {error && <Banner $tone="error">{error}</Banner>}
      {!jobs ? <SpinnerRing size={18} thickness={2} />
        : rows.length === 0 ? <Empty>{t('dashboard.list.empty')}</Empty>
        : (
          <Scroll>
            {rows.map((j) => {
              const state = j.in_flight ? 'RUNNING' : j.last_run?.state
              const tone: Tone = state ? (TONE[state] ?? 'muted') : 'muted'
              const paused = !!j.schedule && !j.enabled
              return (
                <Line key={j.id}>
                  <Dot $tone={tone} />
                  <Main>
                    <div className="t">
                      <Id onClick={() => navigate(`/nomaflow/jobs/${encodeURIComponent(j.id)}`)}>{j.id}</Id>
                      {state && <Pill $tone={tone} style={{ marginTop: 0, marginLeft: 8, verticalAlign: 'middle' }}>{state}</Pill>}
                    </div>
                    <Muted>{when(j)}</Muted>
                  </Main>
                  <span style={{ display: 'flex', gap: 6, flex: 'none' }}>
                    <Btn type="button" title={t('nomaflow.jobs.runNow')} disabled={busy === j.id || j.in_flight} onClick={() => void act(j, 'run')}>
                      {busy === j.id ? <SpinnerRing size={12} thickness={2} /> : <Play size={14} />}
                    </Btn>
                    {j.schedule && (
                      <Btn type="button" title={t(paused ? 'dashboard.jobs.resume' : 'dashboard.jobs.pause')} disabled={busy === j.id} onClick={() => void act(j, paused ? 'resume' : 'pause')}>
                        {paused ? <Play size={14} style={{ color: colors.green.main }} /> : <Pause size={14} />}
                      </Btn>
                    )}
                  </span>
                </Line>
              )
            })}
          </Scroll>
        )}
    </Card>
  )
}
