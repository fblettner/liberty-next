// A nomaflow job for the user (a ``job`` menu item, permission ``job:<id>``): the description, one
// file picker per ``file_load`` step, a Run button, the run's log followed live, the previous runs.
// The technical side (parameters, step toggles, cancel, the catalogue) stays on the nomaflow pages;
// this page talks to ``/api/jobs`` only (services/jobRunner.ts).
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Play, Download, FileSpreadsheet } from 'lucide-react'
import { ApiError } from '../../api/client'
import { Banner, Button, Centered, FileInput, Input, PageLayout, SearchSelect, SpinnerRing, Tag } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { findMenuLabel } from '../../services/menuLabels'
import { splitJobTarget } from '../../services/menuPath'
import { downloadRunFile, durationOf, filesInLog, getJob, getRun, isActive, runJob, uploadFile, type JobRunDetail, type JobRunRow, type UserJob } from '../../services/jobRunner'
import { STATE_TONE, relative } from '../Nomaflow/util'
import { colors, fontSize, fonts, radius } from '../../theme'

const POLL_MS = 2000

const Body = styled.div`display: flex; flex-direction: column; gap: 14px; min-height: 0; flex: 1; overflow: auto; padding-bottom: 12px;`
const Card = styled.section`border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; padding: 14px;`
const Title = styled.h3`margin: 0 0 10px; font-size: ${fontSize.sm}; text-transform: uppercase; letter-spacing: 0.04em; color: ${colors.text.muted};`
const Row = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap;`
const Field = styled.label`display: flex; flex-direction: column; gap: 4px; min-width: 260px; font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const LogBox = styled.pre`
  margin: 0; padding: 12px; max-height: 420px; min-height: 120px; overflow: auto;
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  white-space: pre-wrap; word-break: break-word;
`
const Runs = styled.table`
  width: 100%; border-collapse: collapse; font-size: ${fontSize.sm};
  th { text-align: left; color: ${colors.text.muted}; font-weight: 600; padding: 4px 8px; border-bottom: 1px solid ${colors.border}; }
  td { padding: 5px 8px; border-bottom: 1px solid ${colors.border}; }
  tr[data-pick] { cursor: pointer; }
  tr[data-active='true'] td { background: ${colors.bg.dropdown}; }
`
const Err = styled.span`color: ${colors.red.main};`

export default function JobRunner({ jobId: target }: { jobId: string }) {
  const { t } = useTranslation()
  const { jobId, preset } = splitJobTarget(target)
  const { menus } = useWorkspace()
  const [job, setJob] = useState<UserJob | null>(null)
  const [runs, setRuns] = useState<JobRunRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<Record<string, File | null>>({})
  const [params, setParams] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<'upload' | 'run' | null>(null)
  const [picked, setPicked] = useState<string | null>(null)      // the run whose log is shown
  const [detail, setDetail] = useState<JobRunDetail | null>(null)
  const logRef = useRef<HTMLPreElement | null>(null)

  const title = useMemo(() => findMenuLabel(menus, { kind: 'job', connector: '', target }) ?? (preset ? `${jobId} · ${preset}` : jobId), [menus, target, jobId, preset])

  const loadJob = useCallback(async () => {
    try {
      const r = await getJob(jobId)
      setJob(r.job); setRuns(r.runs); setError(null)
      setParams((cur) => Object.fromEntries(r.job.params.map((p) => [p.name, cur[p.name] ?? p.default ?? ''])))
      setPicked((cur) => cur ?? r.runs[0]?.id ?? null)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
  }, [jobId])
  useEffect(() => { void loadJob() }, [loadJob])

  // the picked run's detail; polled while it is live, the history refreshed when it ends
  const loadRun = useCallback(async () => {
    if (!picked) { setDetail(null); return }
    try { setDetail(await getRun(jobId, picked)) } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
  }, [jobId, picked])
  useEffect(() => { void loadRun() }, [loadRun])
  const active = isActive(detail?.run.state)
  useEffect(() => {
    if (!active) return
    const h = setInterval(() => void loadRun(), POLL_MS)
    return () => clearInterval(h)
  }, [active, loadRun])
  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && !active) void loadJob()
    wasActive.current = active
  }, [active, loadJob])
  useLayoutEffect(() => { if (active && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [detail, active])

  const run = async () => {
    if (!job) return
    const missing = job.uploads.filter((u) => !files[u.step])
    if (missing.length) { setError(t('jobrun.fileRequired')); return }
    const blank = job.params.find((p) => p.required && !(params[p.name] ?? '').trim())
    if (blank) { setError(t('jobrun.paramRequired', { name: blank.label })); return }
    setError(null)
    try {
      const paths: Record<string, string> = {}
      setBusy('upload')
      for (const u of job.uploads) {
        const f = files[u.step]
        if (f) paths[u.step] = (await uploadFile(jobId, f)).path
      }
      setBusy('run')
      const r = await runJob(jobId, paths, params, preset)
      setFiles({})
      setPicked(r.run_id)
      await loadJob()
    } catch (e) { setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)) } finally { setBusy(null) }
  }

  if (error && !job) return <PageLayout icon={<Play size={18} />} title={title}><Banner $tone="error">{error}</Banner></PageLayout>
  if (!job) return <PageLayout icon={<Play size={18} />} title={title}><Centered /></PageLayout>
  const inFlight = job.in_flight || active
  const runFiles = detail ? filesInLog(detail.run.id, detail.logs) : []
  return (
    <PageLayout icon={<Play size={18} />} title={title} description={job.description ?? undefined}>
      <Body>
        {error && <Banner $tone="error">{error}</Banner>}
        <Card>
          <Row>
            {job.params.map((p) => (
              <Field key={p.name}>
                <span>{p.label}{p.required ? ' *' : ''}</span>
                {p.choices.length > 0
                  ? <SearchSelect sort value={params[p.name] ?? ''} onChange={(v) => setParams((c) => ({ ...c, [p.name]: v }))} options={p.choices} anyLabel={p.required ? undefined : t('jobrun.anyChoice')} disabled={busy != null || inFlight} />
                  : <Input value={params[p.name] ?? ''} onChange={(e) => setParams((c) => ({ ...c, [p.name]: e.target.value }))} disabled={busy != null || inFlight} />}
              </Field>
            ))}
            {job.uploads.map((u) => (
              <Field key={u.step}>
                <span><FileSpreadsheet size={12} style={{ verticalAlign: '-2px' }} /> {t('jobrun.fileFor', { step: u.step })}{u.sheet != null && u.sheet !== '' ? ` · ${t('jobrun.sheet', { sheet: String(u.sheet) })}` : ''}</span>
                <FileInput value={files[u.step] ?? null} onChange={(f) => setFiles((p) => ({ ...p, [u.step]: f }))} accept=".xlsx,.xlsm,.csv,.txt,.tsv" disabled={busy != null || inFlight} />
              </Field>
            ))}
            <Button $variant="primary" onClick={() => void run()} disabled={busy != null || inFlight}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : <Play size={14} />} {busy === 'upload' ? t('jobrun.uploading') : inFlight ? t('jobrun.running') : t('jobrun.run')}
            </Button>
            {inFlight && !busy && <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('jobrun.inFlight')}</span>}
          </Row>
        </Card>
        <Card>
          <Title>{t('jobrun.log')}{detail ? <> · <Tag $tone={STATE_TONE[detail.run.state]}>{t(`jobrun.state.${detail.run.state}`)}</Tag> {detail.run.state === 'QUEUED' ? t('jobrun.queued') : ''}</> : null}</Title>
          {detail ? <LogBox ref={logRef}>{detail.logs || t('jobrun.noLog')}</LogBox> : <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('jobrun.noRuns')}</span>}
          {detail?.run.error_message && <div style={{ marginTop: 8, fontSize: fontSize.sm }}><Err>{detail.run.error_message}</Err></div>}
          {runFiles.length > 0 && (
            <Row style={{ marginTop: 10 }}>
              <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('jobrun.files')}</span>
              {runFiles.map((n) => <Button key={n} $size="sm" $variant="ghost" onClick={() => void downloadRunFile(jobId, detail!.run.id, n).catch((e) => setError(String(e)))}><Download size={12} /> {n}</Button>)}
            </Row>
          )}
        </Card>
        <Card>
          <Title>{t('jobrun.history')}</Title>
          {runs.length === 0 ? <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('jobrun.noRuns')}</span> : (
            <Runs>
              <thead><tr><th>{t('jobrun.started')}</th><th /><th>{t('jobrun.duration')}</th><th>{t('jobrun.by')}</th><th /></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} data-pick data-active={r.id === picked} onClick={() => setPicked(r.id)}>
                    <td title={r.started_at ?? ''}>{relative(r.started_at)}</td>
                    <td><Tag $tone={STATE_TONE[r.state]}>{t(`jobrun.state.${r.state}`)}</Tag></td>
                    <td>{durationOf(r)}</td>
                    <td>{r.triggered_by ?? r.trigger_kind}</td>
                    <td>{r.rows_affected != null ? t('jobrun.rows', { n: r.rows_affected }) : ''}{r.error_message ? <Err> {r.error_message.slice(0, 120)}</Err> : null}</td>
                  </tr>
                ))}
              </tbody>
            </Runs>
          )}
        </Card>
      </Body>
    </PageLayout>
  )
}
