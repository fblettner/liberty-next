// nomaflow run detail — run summary + step table + the run's LOG. The point of
// this page: a scheduled job's log is viewable in the UI, not just stdout.
//
// GET /admin/jobs/runs/:runId returns {run, steps, logs}. While the run is
// RUNNING/QUEUED the page polls every 2s — the logs endpoint serves the live
// in-memory buffer for an active run, so the log grows in view even mid-step
// (including a hung step). Once the run reaches a terminal state, polling stops
// and the log is the durable copy.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Download, Pause, Play, RefreshCw, ScrollText, Workflow } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Card, Tag, Mono } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { RunDetailResponse, RunState } from './types'
import { STATE_TONE } from './util'

const POLL_MS = 2000
const ACTIVE: RunState[] = ['RUNNING', 'QUEUED']

const Toolbar = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 14px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1;`
const Section = styled(Card)`display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px;`
const SectionTitle = styled.div`
  display: flex; align-items: center; gap: 7px;
  font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.md};
`
const SummaryGrid = styled.div`
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px;
`
const Field = styled.div`display: flex; flex-direction: column; gap: 2px;`
const FieldLabel = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const FieldValue = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const StepHeaderRow = styled.div`
  display: grid; grid-template-columns: 28px 1.4fr 0.9fr 0.6fr 0.9fr 2fr;
  gap: 10px; padding: 4px 8px; font-size: ${fontSize.sm}; color: ${colors.text.muted};
`
const StepRow = styled.div`
  display: grid; grid-template-columns: 28px 1.4fr 0.9fr 0.6fr 0.9fr 2fr;
  gap: 10px; padding: 7px 8px; align-items: center;
  border-top: 1px solid ${colors.border}; font-size: ${fontSize.sm};
`
const StepCell = styled.span`
  color: ${colors.text.secondary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
`
// The error column — collapsed by default (one line, ellipsis) so the table reads compactly;
// click toggles to a wrapped pre-block that shows the full message. Hover hint communicates
// that it's clickable. Long stack traces stay readable via the scrollable max-height.
const ErrorCell = styled.span<{ $expanded?: boolean }>`
  color: ${({ $expanded }) => ($expanded ? colors.text.primary : colors.text.secondary)};
  cursor: pointer; user-select: text;
  ${({ $expanded }) => $expanded
    ? `white-space: pre-wrap; word-break: break-word; max-height: 240px; overflow: auto;
       background: ${colors.bg.input}; padding: 6px 8px; border-radius: ${radius.sm};
       font-family: ${fonts.mono}; font-size: ${fontSize.sm};`
    : `overflow: hidden; text-overflow: ellipsis; white-space: nowrap;`}
  &:hover { color: ${colors.text.primary}; }
`
const LogBox = styled.pre`
  margin: 0; padding: 12px; max-height: 460px; overflow: auto;
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  white-space: pre-wrap; word-break: break-word;
`
const LiveDot = styled.span`
  display: inline-flex; align-items: center; gap: 5px; font-size: ${fontSize.sm}; color: ${colors.blue.main};
  &::before {
    content: ''; width: 7px; height: 7px; border-radius: 50%;
    background: ${colors.blue.main}; animation: nfpulse 1.4s ease-in-out infinite;
  }
  @keyframes nfpulse { 0%,100% { opacity: 0.3; } 50% { opacity: 1; } }
`
const EmptyLog = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 8px 2px;`
// Right-aligned controls inside the log SectionTitle row (follow toggle + status hint).
const LogControls = styled.div`
  margin-left: auto; display: inline-flex; align-items: center; gap: 8px;
  font-size: ${fontSize.sm}; color: ${colors.text.muted};
`

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

/**
 * @param runIdProp When provided, the component reads its run id from props instead of
 * react-router's ``useParams`` — lets the TabHost render this page inside a workspace tab
 * (the row_click_route on Job Runs opens a ``nomaflow_run`` tab whose id is the run id).
 * Falls back to the route param so the standalone ``/nomaflow/runs/:runId`` route still works.
 */
export default function RunDetail({ runId: runIdProp }: { runId?: string } = {}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const params = useParams()
  const runId = runIdProp ?? params.runId ?? ''
  const [data, setData] = useState<RunDetailResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLPreElement | null>(null)
  // Follow mode (auto-scroll the log to the bottom as new lines arrive).
  // Default: ON while the run is live. Toggled OFF when the operator scrolls
  // up (smart auto-pause — matches Datadog / Grafana / kubectl follow UX) or
  // clicks the Pause button. Resumes automatically when the operator scrolls
  // back to within 8px of the bottom, OR explicitly via the Follow button.
  const [follow, setFollow] = useState(true)
  // Step rows whose error cell the operator has expanded. The cell starts truncated to keep
  // the table dense; click toggles to a wrapped scroll-block. Keyed by ``${step_index}.${attempt}``
  // (one row per retry attempt) so a retried step's per-attempt errors expand independently.
  const [expandedErrors, setExpandedErrors] = useState<Set<string>>(new Set())
  const toggleError = useCallback((key: string) => {
    setExpandedErrors((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }, [])

  const load = useCallback(() => {
    api.get<RunDetailResponse>(`/admin/jobs/runs/${encodeURIComponent(runId)}`)
      .then((r) => { setData(r); setError(null) })
      .catch((e) => setError(e instanceof ApiError
        ? (e.status === 403 ? t('nomaflow.superuserRequired') : e.message)
        : String(e)))
  }, [runId, t])
  useEffect(load, [load])

  // Poll while the run is active — the logs endpoint serves the live buffer,
  // so the log + step states refresh in view. Stop once it's terminal.
  const active = data != null && ACTIVE.includes(data.run.state)
  useEffect(() => {
    if (!active) return
    const h = setInterval(load, POLL_MS)
    return () => clearInterval(h)
  }, [active, load])

  // Keep the log scrolled to the tail while follow is on AND the run is live —
  // useLayoutEffect so the scroll happens before paint (no visible jump).
  // When follow is off the position is left alone so the operator can read
  // earlier lines without the 2-second poll snapping them back to the bottom.
  useLayoutEffect(() => {
    if (active && follow && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [data, active, follow])

  // Smart auto-pause: as soon as the operator scrolls up from the bottom, drop
  // follow. As soon as they scroll back to the bottom (within 8px tolerance —
  // any tighter and a 1px rounding glitch breaks resume), re-enable it. The
  // explicit toggle button still works either way.
  const onLogScroll = useCallback(() => {
    const el = logRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 8
    setFollow((prev) => (prev === atBottom ? prev : atBottom))
  }, [])

  // Save the current log buffer as a .log file. Reuses the blob+anchor pattern
  // from common/DataTable.tsx (the codebase's download convention). Uses the
  // in-view ``data.logs`` rather than re-fetching, so what you download is
  // exactly what you see on screen at click time.
  const downloadLog = useCallback(() => {
    if (!data?.logs) return
    const blob = new Blob([data.logs], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    Object.assign(document.createElement('a'), {
      href: url,
      download: `nomaflow-run-${runId}.log`,
    }).click()
    URL.revokeObjectURL(url)
  }, [data?.logs, runId])

  return (
    <PageLayout
      icon={<Workflow size={18} />}
      title={t('nomaflow.run.title')}
      description={data ? `${data.run.job_id} · ${runId}` : runId}
    >
      <Toolbar>
        {/* Two back links because the Run detail is reachable from two places:
              * the Job Runs list (Operations → Job Runs menu, a query screen at
                ``/sql/nomaflow/list_runs``) — the operator-flow primary, since
                operators triage failed runs by browsing the run list and clicking
                in;
              * the JobsList page (``/nomaflow``) — for the "what's the state of
                THIS job's last run" drill-down (last-run state badge → run detail).
            A single back link forced operators on the wrong starting screen, and
            relying on browser history is unreliable when these open as workspace
            tabs (history may not exist). Two compact ghost buttons solve it. */}
        <Button $variant="ghost" $size="sm" onClick={() => navigate('/sql/nomaflow/list_runs')}>
          <ArrowLeft size={14} /> {t('nomaflow.run.backToRuns', 'Job Runs')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={() => navigate('/nomaflow')}>
          <ArrowLeft size={14} /> {t('nomaflow.editor.backToJobs')}
        </Button>
        <Spacer />
        {active && <LiveDot>{t('nomaflow.run.live')}</LiveDot>}
        <Button $variant="ghost" $size="sm" onClick={load}>
          <RefreshCw size={14} /> {t('common.reload')}
        </Button>
      </Toolbar>

      {error && <Banner $tone="error">{error}</Banner>}
      {!data && !error && <Centered />}

      {data && (
        <>
          <Section>
            <SectionTitle>
              {t('nomaflow.run.summary')}
              <Tag $tone={STATE_TONE[data.run.state]}>{data.run.state}</Tag>
            </SectionTitle>
            <SummaryGrid>
              <Field>
                <FieldLabel>{t('nomaflow.run.job')}</FieldLabel>
                <FieldValue><Mono>{data.run.job_id}</Mono></FieldValue>
              </Field>
              <Field>
                <FieldLabel>{t('nomaflow.run.trigger')}</FieldLabel>
                <FieldValue>{data.run.trigger_kind}{data.run.triggered_by ? ` · ${data.run.triggered_by}` : ''}</FieldValue>
              </Field>
              <Field>
                <FieldLabel>{t('nomaflow.run.started')}</FieldLabel>
                <FieldValue>{fmt(data.run.started_at)}</FieldValue>
              </Field>
              <Field>
                <FieldLabel>{t('nomaflow.run.finished')}</FieldLabel>
                <FieldValue>{fmt(data.run.finished_at)}</FieldValue>
              </Field>
              <Field>
                <FieldLabel>{t('nomaflow.run.rows')}</FieldLabel>
                <FieldValue>{data.run.rows_affected ?? '—'}</FieldValue>
              </Field>
            </SummaryGrid>
            {data.run.error_message && (
              <Banner $tone="error">{data.run.error_message}</Banner>
            )}
          </Section>

          <Section>
            <SectionTitle>{t('nomaflow.run.steps', { count: data.steps.length })}</SectionTitle>
            <div>
              <StepHeaderRow>
                <span>#</span>
                <span>{t('nomaflow.steps.name')}</span>
                <span>{t('nomaflow.run.colType')}</span>
                <span>{t('nomaflow.run.colAttempt')}</span>
                <span>{t('nomaflow.run.colState')}</span>
                <span>{t('nomaflow.run.colError')}</span>
              </StepHeaderRow>
              {data.steps.map((s) => {
                const key = `${s.step_index}.${s.attempt}`
                const expanded = expandedErrors.has(key)
                const err = s.error_message ?? ''
                return (
                  <StepRow key={key}>
                    <StepCell>{s.step_index}</StepCell>
                    <StepCell><Mono>{s.step_name}</Mono></StepCell>
                    <StepCell>{s.step_type}</StepCell>
                    <StepCell>{s.attempt}</StepCell>
                    <StepCell><Tag $tone={STATE_TONE[s.state]}>{s.state}</Tag></StepCell>
                    {/* Errors are long (psycopg messages, stack traces) so the cell starts
                        truncated and clicks toggle a wrapped scroll-block. Empty errors aren't
                        clickable — there's nothing to expand. */}
                    <ErrorCell
                      $expanded={expanded && err.length > 0}
                      onClick={err ? () => toggleError(key) : undefined}
                      title={!expanded && err ? t('nomaflow.run.clickToExpand', 'Click to expand') : undefined}
                    >
                      {err}
                    </ErrorCell>
                  </StepRow>
                )
              })}
            </div>
          </Section>

          <Section>
            <SectionTitle>
              <ScrollText size={15} /> {t('nomaflow.run.log')}
              {/* Right-aligned log controls. The Download button is always
                  available when there's a log to download (live OR terminal);
                  the Follow toggle only matters while the run is streaming. */}
              <LogControls>
                {active && (
                  <>
                    <span>{follow ? t('nomaflow.run.followOn') : t('nomaflow.run.followOff')}</span>
                    <Button $variant="ghost" $size="sm" onClick={() => setFollow((v) => !v)}>
                      {follow
                        ? <><Pause size={14} /> {t('nomaflow.run.followPause')}</>
                        : <><Play size={14} /> {t('nomaflow.run.followResume')}</>}
                    </Button>
                  </>
                )}
                {data.logs && (
                  <Button $variant="ghost" $size="sm" onClick={downloadLog}>
                    <Download size={14} /> {t('nomaflow.run.downloadLog')}
                  </Button>
                )}
              </LogControls>
            </SectionTitle>
            {data.logs
              ? <LogBox ref={logRef} onScroll={onLogScroll}>{data.logs}</LogBox>
              : <EmptyLog>{t('nomaflow.run.noLog')}</EmptyLog>}
          </Section>
        </>
      )}
    </PageLayout>
  )
}
