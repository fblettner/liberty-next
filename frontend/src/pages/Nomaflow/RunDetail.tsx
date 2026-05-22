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
import { ArrowLeft, RefreshCw, ScrollText, Workflow } from 'lucide-react'
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

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

export default function RunDetail() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { runId = '' } = useParams()
  const [data, setData] = useState<RunDetailResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLPreElement | null>(null)

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

  // Keep the log scrolled to the tail while the run is live (new lines arrive
  // at the bottom). useLayoutEffect so the scroll happens before paint.
  useLayoutEffect(() => {
    if (active && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [data, active])

  return (
    <PageLayout
      icon={<Workflow size={18} />}
      title={t('nomaflow.run.title')}
      description={data ? `${data.run.job_id} · ${runId}` : runId}
    >
      <Toolbar>
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
              {data.steps.map((s) => (
                <StepRow key={`${s.step_index}.${s.attempt}`}>
                  <StepCell>{s.step_index}</StepCell>
                  <StepCell><Mono>{s.step_name}</Mono></StepCell>
                  <StepCell>{s.step_type}</StepCell>
                  <StepCell>{s.attempt}</StepCell>
                  <StepCell><Tag $tone={STATE_TONE[s.state]}>{s.state}</Tag></StepCell>
                  <StepCell title={s.error_message ?? ''}>{s.error_message ?? ''}</StepCell>
                </StepRow>
              ))}
            </div>
          </Section>

          <Section>
            <SectionTitle><ScrollText size={15} /> {t('nomaflow.run.log')}</SectionTitle>
            {data.logs
              ? <LogBox ref={logRef}>{data.logs}</LogBox>
              : <EmptyLog>{t('nomaflow.run.noLog')}</EmptyLog>}
          </Section>
        </>
      )}
    </PageLayout>
  )
}
