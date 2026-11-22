// The tests of the installation's applications, edited like a service: the suites and their cases on the
// left, the picked case drawn as a flow of calls — each card an endpoint called and what is expected of its
// answer, coloured by its last result — and what is picked edited beside it: the case at its start, a step
// (its endpoint, its parameters as the endpoint's own form, what it expects — filled from the live answer).
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import styled from '@emotion/styled'
import { CheckCircle2, CircleDashed, FlaskConical, Flag, Play, Plus, Save, Trash2, Wand2, XCircle } from 'lucide-react'
import { Banner, Button, Centered, Checkbox, Field, Input, SearchSelect, Textarea, useModals } from '../../common'
import { api, ApiError } from '../../api/client'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { useAuth } from '../../auth/AuthContext'
import { hasPermission } from '../../services/permissions'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'
import { FlowCanvas, FlowRows } from './flowKit'
import { PairsField } from '../TableView/PairsField'
import {
  type CaseRun, type TestCase, type TestExpect, type TestStep, type TestSuite, expectSummary, freshStepName, linesList, linesText, pairsObject, pairsText, tidyStep,
} from '../../services/appTests'
import { useDirtyGuard } from '../../tabs/dirtyGuard'

interface SuiteLine { name: string; label?: string | null; connector?: string | null; error?: string; cases: { id: string; label: string | null; writes: boolean; steps: number; last: { status: string; at: string } | null }[] }
interface Loaded { name: string; suite: TestSuite; results: Record<string, CaseRun[]>; has_reference: boolean }
type Tone = 'green' | 'red' | 'orange' | 'blue'
const TONE: Record<string, Tone> = { passed: 'green', failed: 'red', changed: 'orange' }

const Page = styled.div`display: flex; gap: 12px; height: calc(100vh - 150px); min-height: 520px;`
const Left = styled.div`flex: 0 0 250px; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding: 10px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};`
const SuiteName = styled.div`display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.secondary};`
const CaseRow = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: ${radius.md}; cursor: pointer; text-align: left; font-family: ${fonts.sans};
  background: ${(p) => (p.$on ? colors.blue.bg : 'transparent')}; border: 1px solid ${(p) => (p.$on ? colors.blue.border : 'transparent')};
  color: ${colors.text.primary}; font-size: ${fontSize.sm};
  &:hover { border-color: ${colors.border}; }
  & span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const Mid = styled.div`flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 10px;`
const Bar = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap;`
const Side = styled.div`
  flex: 0 0 min(480px, 36%); min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding: 14px 16px;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  & h3 { margin: 0; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; }
  & .note { color: ${colors.text.muted}; font-size: ${fontSize.sm}; line-height: 1.5; }
  & .two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 10px; }
  & .three { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  & .found { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.red.main}; white-space: pre-wrap; overflow-wrap: anywhere; }
`
const Line = styled.div`display: flex; align-items: center; width: max-content;`
const Wire = styled.div`
  position: relative; flex: 0 0 40px; height: 2px; background: ${colors.border};
  &::after { content: ''; position: absolute; right: 0; top: -3px; border-left: 6px solid ${colors.border}; border-top: 4px solid transparent; border-bottom: 4px solid transparent; }
  & button { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 20px; height: 20px; padding: 0; cursor: pointer; display: inline-flex;
    align-items: center; justify-content: center; border-radius: 999px; background: var(--bg-modal); color: ${colors.text.muted}; border: 1px solid ${colors.border}; }
  & button:hover { color: ${colors.blue.main}; border-color: ${colors.blue.main}; background: ${colors.blue.bg}; }
`
const End = styled.button<{ $on?: boolean; $tone: Tone }>`
  flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 9px 14px; cursor: pointer; font-family: ${fonts.sans}; border-radius: 999px;
  background: ${(p) => colors[p.$tone].bg}; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-weight: 600;
  border: 1px solid ${(p) => (p.$on ? colors[p.$tone].main : colors[p.$tone].border)};
`
const Card = styled.button<{ $on: boolean; $tone: Tone | null }>`
  position: relative; flex: 0 0 auto; width: 210px; text-align: left; cursor: pointer; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px;
  font-family: ${fonts.sans}; background: var(--bg-modal); border-radius: ${radius.lg};
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : p.$tone ? colors[p.$tone].border : colors.border)}; box-shadow: ${(p) => (p.$on ? shadow.focus : 'none')};
  &:hover { transform: translateY(-1px); }
  & .kind { display: flex; align-items: center; gap: 6px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
    color: ${(p) => (p.$tone ? colors[p.$tone].main : colors.text.muted)}; }
  & .name { font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .what { font-size: ${fontSize.micro}; font-family: ${fonts.mono}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const Run = styled.div<{ $tone: Tone | null }>`
  display: flex; align-items: baseline; gap: 8px; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & b { color: ${(p) => (p.$tone ? colors[p.$tone].main : colors.text.muted)}; font-weight: 600; }
  & i { margin-left: auto; font-style: normal; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
`

/** Does the step use what the Advanced section holds? It then opens by itself. */
function advancedUsed(step: TestStep): boolean {
  const e = step.expect ?? {}
  const some = (v: unknown) => (Array.isArray(v) ? v.length > 0 : v != null && typeof v === 'object' ? Object.keys(v).length > 0 : !!v)
  return [step.capture, step.rows, step.keep, e.equals, e.contains, e.present, e.absent, e.warning_contains].some(some)
}

function statusIcon(status: string | undefined, size = 14): ReactNode {
  if (status === 'passed') return <CheckCircle2 size={size} color={colors.green.main} />
  if (status === 'failed') return <XCircle size={size} color={colors.red.main} />
  if (status === 'changed') return <XCircle size={size} color={colors.orange.main} />
  return <CircleDashed size={size} color={colors.text.muted} />
}
const when = (iso: string): string => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
const num = (v: string): number | null => (v.trim() === '' || Number.isNaN(Number(v)) ? null : Math.max(0, Math.trunc(Number(v))))

export default function TestsBuilder() {
  const { t } = useTranslation()
  // what the routes allow: reading needs tests:view, playing tests:run, writing tests:edit (which also plays).
  // A button the caller may not use is not shown — the server would refuse it anyway.
  const { user } = useAuth()
  const may = (...perms: string[]) => !!user && (user.is_superuser || perms.some((p) => hasPermission(user.permissions ?? [], p)))
  const mayEdit = may('tests:edit'), mayRun = may('tests:run', 'tests:edit')
  const modals = useModals()
  const { connectors } = useWorkspace()
  const [suites, setSuites] = useState<SuiteLine[] | null>(null)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [doc, setDoc] = useState<TestSuite | null>(null)          // the suite being edited
  const [caseId, setCaseId] = useState<string | null>(null)
  const [stepAt, setStepAt] = useState<number | null>(null)         // null = the case itself (the start)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [writes, setWrites] = useState(false)

  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : String(e))
  const list = useCallback(() => api.get<{ suites: SuiteLine[] }>('/admin/tests').then((r) => setSuites(r.suites)).catch(fail), [])
  const take = (l: Loaded) => { setLoaded(l); setDoc(l.suite) }
  const open = useCallback((name: string, id?: string | null) => {
    setError(null)
    api.get<Loaded>(`/admin/tests/${encodeURIComponent(name)}`).then((l) => { take(l); setCaseId(id ?? l.suite.case[0]?.id ?? null); setStepAt(null) }).catch(fail)
  }, [])
  useEffect(() => { void list() }, [list])
  const [search] = useSearchParams()
  useEffect(() => {
    if (!suites || loaded || suites.length === 0) return
    const asked = suites.find((x) => x.name === search.get('suite') && !x.error)
    if (asked) open(asked.name, search.get('case'))
    else if (!suites[0].error) open(suites[0].name)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the link is read once, when the suites arrive
  }, [suites, loaded, open])

  const dirty = !!loaded && !!doc && JSON.stringify(doc) !== JSON.stringify(loaded.suite)
  useDirtyGuard(dirty)          // the shell asks before a closed tab or a reload loses these edits
  const current = doc?.case.find((c) => c.id === caseId) ?? null
  const step = current && stepAt != null ? current.steps[stepAt] ?? null : null
  const lastRun = (loaded && caseId ? loaded.results[caseId]?.[0] : undefined) ?? null
  const apiConnectors = useMemo(() => (connectors ?? []).filter((c) => c.type === 'api'), [connectors])
  const target = current?.connector || doc?.connector || ''
  const endpoints = useMemo(() => { const c = apiConnectors.find((x) => x.name === target); return c && 'endpoints' in c ? c.endpoints : [] }, [apiConnectors, target])

  const patchCase = (p: Partial<TestCase>) => { if (!doc || !current) return; setDoc({ ...doc, case: doc.case.map((c) => (c === current ? { ...c, ...p } : c)) }); if (p.id) setCaseId(p.id) }
  const patchStep = (p: Partial<TestStep>) => { if (!current || stepAt == null) return; patchCase({ steps: current.steps.map((s, i) => (i === stepAt ? { ...s, ...p } : s)) }) }
  const patchExpect = (p: Partial<TestExpect>) => patchStep({ expect: { ...(step?.expect ?? {}), ...p } })
  const insert = (index: number) => {
    if (!current) return
    const fresh: TestStep = { name: freshStepName(current.steps), endpoint: '' }
    patchCase({ steps: [...current.steps.slice(0, index), fresh, ...current.steps.slice(index)] }); setStepAt(index)
  }

  const save = async (): Promise<Loaded | null> => {
    if (!loaded || !doc) return null
    setBusy(true); setError(null)
    try {
      const body = { ...doc, case: doc.case.map((c) => ({ ...c, steps: c.steps.map(tidyStep) })) }
      const l = await api.put<Loaded>(`/admin/tests/${encodeURIComponent(loaded.name)}`, body)
      take(l); void list(); return l
    } catch (e) { fail(e); return null } finally { setBusy(false) }
  }
  const run = async (only: string | null, mode: 'compare' | 'record') => {
    if (!loaded) return
    const now = dirty ? await save() : loaded
    if (!now) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post<{ summary: Record<string, number> }>(`/admin/tests/${encodeURIComponent(now.name)}/run`, { case: only, mode, allow_writes: writes })
      setNotice(t(mode === 'record' ? 'tests.recorded' : 'tests.ran', r.summary))
      take(await api.get<Loaded>(`/admin/tests/${encodeURIComponent(now.name)}`)); void list()
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  const fill = async () => {
    if (!step) return
    setBusy(true); setError(null)
    try {
      const r = await api.post<{ ok: boolean; error?: string; rows?: number | null; first_row?: Record<string, unknown> }>(
        '/admin/tests/try', { connector: target, endpoint: step.endpoint, params: step.params ?? {}, rows: step.rows ?? null })
      if (!r.ok) setError(r.error ?? t('settings.studio.failed'))
      else if (r.rows == null) setNotice(t('tests.fillNoRows'))
      else { patchExpect({ rows_min: r.rows > 0 ? 1 : null, first_row: r.first_row ?? {} }); setNotice(t('tests.filled', { n: r.rows })) }
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  const newSuite = async () => {
    const typed = await modals.prompt({ title: t('tests.newSuite'), message: t('tests.newSuitePrompt') })
    const name = (typed ?? '').trim().replace(/[^A-Za-z0-9_-]+/g, '-')
    if (!name) return
    try { take(await api.put<Loaded>(`/admin/tests/${encodeURIComponent(name)}`, { connector: apiConnectors[0]?.name ?? null, case: [] })); setCaseId(null); void list() } catch (e) { fail(e) }
  }
  const newCase = () => {
    if (!doc) return
    let n = doc.case.length + 1
    while (doc.case.some((c) => c.id === `case-${n}`)) n += 1
    setDoc({ ...doc, case: [...doc.case, { id: `case-${n}`, steps: [] }] }); setCaseId(`case-${n}`); setStepAt(null)
  }
  const removeSuite = async () => {
    if (!loaded || !(await modals.confirm({ title: t('tests.deleteSuite'), message: t('tests.deleteSuiteConfirm', { name: loaded.name }), variant: 'danger' }))) return
    try { await api.del(`/admin/tests/${encodeURIComponent(loaded.name)}`); setLoaded(null); setDoc(null); setCaseId(null); void list() } catch (e) { fail(e) }
  }

  if (!suites) return error ? <Banner $tone="error">{error}</Banner> : <Centered />
  const stepRun = (name: string) => lastRun?.steps.find((s) => s.step === name)

  let side: ReactNode = <span className="note">{t('tests.pick')}</span>
  if (doc && current && step) {
    const e = step.expect ?? {}
    const seen = stepRun(step.name)
    side = (
      <>
        <h3>{t('tests.step')}</h3>
        <div className="two">
          <Field label={t('tests.stepName')}><Input value={step.name} onChange={(ev) => patchStep({ name: ev.target.value })} /></Field>
          <Field label={t('tests.endpoint')}>
            <SearchSelect value={step.endpoint} options={endpoints.map((x) => ({ value: x.name, label: x.label || x.name, mono: x.name }))}
              onChange={(v: string | null) => patchStep({ endpoint: v ?? '' })} placeholder={t('common.pick')} />
          </Field>
        </div>
        <Field label={t('tests.params')}>
          <PairsField value={pairsText(step.params)} defs={endpoints.find((x) => x.name === step.endpoint)?.params ?? null} pickFirst={!step.endpoint}
            onChange={(text) => patchStep({ params: pairsObject(text) ?? {} })} />
        </Field>
        <Bar>
          <h3 style={{ flex: 1 }}>{t('tests.expected')}</h3>
          {mayRun && <Button $variant="ghost" $size="sm" disabled={busy || !step.endpoint} onClick={() => void fill()}><Wand2 size={13} /> {t('tests.fill')}</Button>}
        </Bar>
        <div className="two">
          <Field label={t('tests.outcome')}>
            <SearchSelect value={e.success === false ? 'refused' : 'works'} options={[{ value: 'works', label: t('tests.works') }, { value: 'refused', label: t('tests.refused') }]}
              onChange={(v: string | null) => patchExpect({ success: v !== 'refused' })} />
          </Field>
          {e.success === false && <Field label={t('tests.refusalSays')}><Input value={e.error_contains ?? ''} onChange={(ev) => patchExpect({ error_contains: ev.target.value || null })} /></Field>}
        </div>
        {e.success !== false && (
          <>
            <div className="three">
              <Field label={t('tests.rowsMin')}><Input type="number" value={e.rows_min ?? ''} onChange={(ev) => patchExpect({ rows_min: num(ev.target.value) })} /></Field>
              <Field label={t('tests.rowsExact')}><Input type="number" value={e.rows ?? ''} onChange={(ev) => patchExpect({ rows: num(ev.target.value) })} /></Field>
              <Field label={t('tests.rowsMax')}><Input type="number" value={e.rows_max ?? ''} onChange={(ev) => patchExpect({ rows_max: num(ev.target.value) })} /></Field>
            </div>
            <Field label={t('tests.firstRow')}><PairsField value={pairsText(e.first_row)} onChange={(text) => patchExpect({ first_row: pairsObject(text) ?? {} })} /></Field>
          </>
        )}
        <Checkbox checked={!!step.always} onChange={(v: boolean) => patchStep({ always: v || undefined })} label={t('tests.always')} />
        <details key={`${current.id}/${stepAt}`} open={advancedUsed(step) || undefined}>
          <summary className="note" style={{ cursor: 'pointer' }}>{t('tests.advanced')}</summary>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
            <Field label={t('tests.capture')}><PairsField value={pairsText(step.capture)} onChange={(text) => patchStep({ capture: pairsObject(text) ?? {} })} /></Field>
            <Field label={t('tests.rowsAt')}><Input value={step.rows ?? ''} onChange={(ev) => patchStep({ rows: ev.target.value || null })} /></Field>
            <Field label={t('tests.equals')}><PairsField value={pairsText(e.equals)} onChange={(text) => patchExpect({ equals: pairsObject(text) ?? {} })} /></Field>
            <Field label={t('tests.contains')}><PairsField value={pairsText(e.contains)} onChange={(text) => patchExpect({ contains: pairsObject(text) ?? {} })} /></Field>
            <div className="two" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)' }}>
              <Field label={t('tests.present')}><Textarea value={linesText(e.present)} rows={3} style={{ minHeight: 0, fontFamily: fonts.mono }} onChange={(ev) => patchExpect({ present: linesList(ev.target.value) ?? [] })} /></Field>
              <Field label={t('tests.absent')}><Textarea value={linesText(e.absent)} rows={3} style={{ minHeight: 0, fontFamily: fonts.mono }} onChange={(ev) => patchExpect({ absent: linesList(ev.target.value) ?? [] })} /></Field>
            </div>
            <Field label={t('tests.keep')}><Textarea value={linesText(step.keep)} rows={3} style={{ minHeight: 0, fontFamily: fonts.mono }} onChange={(ev) => patchStep({ keep: linesList(ev.target.value) ?? [] })} /></Field>
            <Field label={t('tests.warningSays')}><Input value={e.warning_contains ?? ''} onChange={(ev) => patchExpect({ warning_contains: ev.target.value || null })} /></Field>
          </div>
        </details>
        {seen && seen.problems.length > 0 && <div><h3>{t('tests.found')}</h3><div className="found">{seen.problems.join('\n')}</div></div>}
        {mayEdit && <Bar><Button $variant="ghost" $size="sm" style={{ color: colors.red.main }} onClick={() => { patchCase({ steps: current.steps.filter((_s, i) => i !== stepAt) }); setStepAt(null) }}>
          <Trash2 size={13} /> {t('tests.deleteStep')}</Button></Bar>}
      </>
    )
  } else if (doc && current) {
    side = (
      <>
        <h3>{t('tests.case')}</h3>
        <div className="two">
          <Field label={t('tests.caseId')}><Input value={current.id} style={{ fontFamily: fonts.mono }} onChange={(ev) => patchCase({ id: ev.target.value.replace(/[^A-Za-z0-9_.-]/g, '-') })} /></Field>
          <Field label={t('tests.connector')}>
            <SearchSelect value={doc.connector ?? ''} options={apiConnectors.map((c) => ({ value: c.name, label: c.name }))} onChange={(v: string | null) => setDoc({ ...doc, connector: v || null })} placeholder={t('common.pick')} />
          </Field>
        </div>
        <Field label={t('tests.caseLabel')}><Input value={current.label ?? ''} onChange={(ev) => patchCase({ label: ev.target.value || null })} /></Field>
        <Checkbox checked={!!current.writes} onChange={(v: boolean) => patchCase({ writes: v || undefined })} label={t('tests.writes')} />
        <span className="note">{t('tests.caseHint')}</span>
        {(loaded?.results[current.id] ?? []).length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h3>{t('tests.lastRuns')}</h3>
            {(loaded?.results[current.id] ?? []).slice(0, 8).map((r, i) => <Run key={i} $tone={TONE[r.status] ?? null}>{statusIcon(r.status)}<b>{r.status}</b>{r.mode === 'record' && <span>{t('tests.reference')}</span>}<i>{when(r.at)}</i></Run>)}
          </div>
        )}
        {mayEdit && <Bar><Button $variant="ghost" $size="sm" style={{ color: colors.red.main }} onClick={() => { setDoc({ ...doc, case: doc.case.filter((c) => c !== current) }); setCaseId(null) }}>
          <Trash2 size={13} /> {t('tests.deleteCase')}</Button></Bar>}
      </>
    )
  }

  return (
    <Page>
      <Left>
        {mayEdit && <Button $variant="ghost" $size="sm" onClick={() => void newSuite()}><Plus size={13} /> {t('tests.newSuite')}</Button>}
        {suites.length === 0 && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('tests.none')}</span>}
        {suites.map((s) => {
          const mine = loaded?.name === s.name
          const cases = mine && doc ? doc.case.map((c) => ({ id: c.id, label: c.label ?? null, last: s.cases.find((x) => x.id === c.id)?.last ?? null })) : s.cases
          return (
            <div key={s.name} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <SuiteName><FlaskConical size={13} /> {s.label || s.name}</SuiteName>
              {s.error && <span style={{ color: colors.red.main, fontSize: fontSize.micro }}>{s.error}</span>}
              {cases.map((c) => (
                <CaseRow key={c.id} type="button" $on={mine && caseId === c.id} onClick={() => { if (mine) { setCaseId(c.id); setStepAt(null) } else open(s.name, c.id) }}>
                  {statusIcon(c.last?.status)}<span>{c.label || c.id}</span>
                </CaseRow>
              ))}
              {mine && mayEdit && <CaseRow type="button" $on={false} onClick={newCase} style={{ color: colors.text.muted }}><Plus size={13} /><span>{t('tests.newCase')}</span></CaseRow>}
              {!mine && s.cases.length === 0 && !s.error && <CaseRow type="button" $on={false} onClick={() => open(s.name)} style={{ color: colors.text.muted }}><span>{t('tests.openSuite')}</span></CaseRow>}
            </div>
          )
        })}
      </Left>
      <Mid>
        <Bar>
          {mayEdit && <Button $variant="primary" $size="sm" disabled={busy || !dirty} onClick={() => void save()}><Save size={13} /> {t('common.save')}</Button>}
          {mayRun && <Button $variant="ghost" $size="sm" disabled={busy || !current} onClick={() => void run(caseId, 'compare')}><Play size={13} /> {t('tests.runCase')}</Button>}
          {mayRun && <Button $variant="ghost" $size="sm" disabled={busy || !loaded} onClick={() => void run(null, 'compare')}><Play size={13} /> {t('tests.runSuite')}</Button>}
          {mayRun && <Button $variant="ghost" $size="sm" disabled={busy || !loaded} title={t('tests.recordHint')} onClick={() => void run(null, 'record')}><Flag size={13} /> {t('tests.record')}</Button>}
          {mayRun && <Checkbox checked={writes} onChange={setWrites} label={t('tests.allowWrites')} />}
          {!mayEdit && <span data-tests-readonly style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t(mayRun ? 'tests.mayRunOnly' : 'tests.mayViewOnly')}</span>}
          <span style={{ flex: 1 }} />
          {loaded && mayEdit && <Button $variant="ghost" $size="sm" style={{ color: colors.red.main }} onClick={() => void removeSuite()}><Trash2 size={13} /> {t('tests.deleteSuite')}</Button>}
        </Bar>
        {error && <Banner $tone="error">{error}</Banner>}
        {notice && !error && <Banner $tone="ok">{notice}</Banner>}
        {current ? (
          <FlowCanvas zoomKey="test">
            <FlowRows>
              <End type="button" $tone="blue" $on={stepAt == null} onClick={() => setStepAt(null)}>{statusIcon(lastRun?.status, 15)} {current.label || current.id}</End>
              {current.steps.map((s, i) => {
                const seen = stepRun(s.name)
                return (
                  <Line key={i}>
                    <Wire><button type="button" title={t('settings.studio.addStep')} onClick={() => insert(i)}><Plus size={12} /></button></Wire>
                    <Card type="button" $on={stepAt === i} $tone={seen ? TONE[seen.status] ?? null : null} onClick={() => setStepAt(i)}>
                      <span className="kind">{statusIcon(seen?.status, 13)}{s.endpoint || t('tests.noEndpoint')}</span>
                      <span className="name">{s.name}</span>
                      <span className="what">{expectSummary(s)}</span>
                    </Card>
                  </Line>
                )
              })}
              <Line>
                <Wire><button type="button" title={t('settings.studio.addStep')} onClick={() => insert(current.steps.length)}><Plus size={12} /></button></Wire>
                <End as="div" $tone="green" style={{ cursor: 'default' }}><Flag size={15} /> {t('nomaflow.flow.end')}</End>
              </Line>
            </FlowRows>
          </FlowCanvas>
        ) : <Banner $tone="info">{t(loaded ? 'tests.pickCase' : 'tests.pickSuite')}</Banner>}
      </Mid>
      <Side>{side}</Side>
    </Page>
  )
}
