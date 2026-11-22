// nomaflow step-pipeline editor (NOMAFLOW-UI.md §4) — the ETL-building surface.
// An ordered list of step cards: collapse/expand, up/down reorder, duplicate, delete;
// a per-type "+ Add" toolbar at the bottom.
//
// Increment 4 ships the hand-written sql_copy + sql_query forms (they need live
// connector/query dropdowns). python / ldap_sync / http get a name field + a
// raw-values fallback until increment 5 brings their SchemaForm.
import { useRef, useEffect, useMemo, useState, type ReactNode } from 'react'
import JobFlow from './JobFlow'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, ArrowUp, ArrowDown, Trash2, Copy } from 'lucide-react'
import { Input, Checkbox, Tag, StringListEditor, SearchSelect, CallableSelect, type SearchSelectOption, PlaceholderPicker } from '../../common'
import { NOTIFY_STEP_TOKENS } from '../../services/placeholders'
import { isDirectory, poolSummary, urlParts } from '../../services/poolUrl'
import { api } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { ConnectorMeta } from '../../types/connectors'
import type { StepConfig, StepType } from './types'
import { asOptions, connectorOptions, Field, FieldLabel, Grid, IconBtn, KeyValueEditor, SubHead } from './stepFields'
import FileLoadForm from './FileLoadForm'
import { fileSourceSummary } from '../../services/fileLoad'

// ── styled ───────────────────────────────────────────────────────────────────────
// the job as a flow: the canvas is the workspace, what is picked (a step, or the job at its start) is edited beside it
const FlowSplit = styled.div`display: flex; gap: 14px; height: calc(100vh - 190px); min-height: 520px;`
const FlowSide = styled.div`
  flex: 0 0 min(560px, 44%); min-height: 0; overflow-y: auto; padding: 14px 16px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
`
const Card = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const Head = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  background: ${colors.bg.input}; cursor: pointer;
`
const HeadName = styled.span`font-family: ${fonts.mono}; color: ${colors.text.primary};`
const HeadSummary = styled.span`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.muted};
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
`
const Body = styled.div`padding: 12px; display: flex; flex-direction: column; gap: 12px;`
const TextArea = styled.textarea`
  width: 100%; min-height: 72px; padding: 8px 10px; resize: vertical;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input}; color: ${colors.text.primary};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; outline: none;
  &:focus { border-color: ${colors.blue.main}; }
`

const STEP_TONE: Record<StepType, 'blue' | 'green' | 'orange' | 'purple' | 'neutral' | 'yellow'> = {
  sql_copy: 'blue', sql_query: 'green', python: 'purple', ldap_sync: 'orange', http: 'neutral', call_job: 'yellow', file_load: 'green', report: 'purple', notify: 'blue', file_export: 'orange',
}
const STEP_TYPES: StepType[] = ['sql_copy', 'sql_query', 'python', 'ldap_sync', 'http', 'call_job', 'file_load', 'file_export', 'report', 'notify']

/** A fresh blank step of *type* — the "+ Add" buttons append one of these. */
function blankStep(type: StepType): StepConfig {
  switch (type) {
    case 'sql_copy':
      return { type, name: '', mode: 'overwrite', type_coercion: 'jde', source: {}, target: {} }
    case 'sql_query':
      return { type, name: '', connector: '', query: '' }
    case 'python':
      return { type, name: '', callable: '' }
    case 'ldap_sync':
      return { type, name: '', server: '', bind_dn: '', search_base: '', target_connector: '', target_query: '' }
    case 'http':
      return { type, name: '', url: '', method: 'GET' }
    case 'call_job':
      return { type, name: '', target_job_id: '' }
    case 'file_load':
      return { type, name: '', path: '', target: {}, load_mode: 'append', on_error: 'fail' }
    case 'report':
      return { type, name: '', report: 'analysis:', app: '', model: '', format: 'xlsx', period: 'lastMonth' }
    case 'notify':
      return { type, name: '', recipients: [], subject: '', attach: ['*'], channels: ['portal', 'email'] }
    case 'file_export':
      return { type, name: '', connector: '', query: '', format: 'xlsx' }
  }
}

function stepSummary(s: StepConfig): string {
  const f = s as Record<string, unknown>
  const ep = (e: unknown) => {
    const o = (e ?? {}) as Record<string, unknown>
    return [o.connector, o.schema, o.table].filter(Boolean).join('.')
  }
  switch (s.type) {
    case 'sql_copy': return `${ep(f.source)} → ${ep(f.target)}`
    case 'sql_query': return `${f.connector ?? '?'}.${f.query ?? '?'}`
    case 'python': return String(f.callable ?? '')
    case 'ldap_sync': return String(f.server ?? '')
    case 'http': return `${f.method ?? 'GET'} ${f.url ?? ''}`
    case 'call_job': return `→ ${f.target_job_id ?? '?'}`
    case 'file_load': return `${fileSourceSummary(f)} → ${ep(f.target)}`.trim()
    case 'report': return `${String(f.report ?? '')} → ${String(f.format ?? 'xlsx')}${f.as_user ? ` as ${f.as_user}` : f.as_role ? ` as role ${f.as_role}` : ''}`
    case 'notify': return `${((f.recipients as string[] | undefined) ?? []).join(', ') || '?'} · ${String(f.subject ?? '')}`
    case 'file_export': return `${f.connector ?? '?'}.${f.query ?? '?'} → ${String(f.filename || f.query || '?')}.${String(f.format ?? 'xlsx')}`
    default: return ''
  }
}

// ── per-type forms ─────────────────────────────────────────────────────────────────
interface FormProps {
  step: StepConfig
  patch: (p: Partial<StepConfig>) => void
  sqlConnectors: ConnectorMeta[]
}

function SqlCopyForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const src = (f.source ?? {}) as Record<string, unknown>
  const tgt = (f.target ?? {}) as Record<string, unknown>
  const setEndpoint = (key: 'source' | 'target', part: Record<string, unknown>) =>
    patch({ [key]: { ...(f[key] as object ?? {}), ...part } } as Partial<StepConfig>)
  return (
    <>
      <SubHead>{t('nomaflow.steps.source')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
          <SearchSelect sort clearable value={String(src.connector ?? '')} onChange={(v) => setEndpoint('source', { connector: v })}
            options={connectorOptions(sqlConnectors)} placeholder="—" />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.schema')}</FieldLabel>
          <Input value={String(src.schema ?? '')} onChange={(e) => setEndpoint('source', { schema: e.target.value })} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.table')}</FieldLabel>
          <Input value={String(src.table ?? '')} onChange={(e) => setEndpoint('source', { table: e.target.value })} />
        </Field>
      </Grid>
      <SubHead>{t('nomaflow.steps.target')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
          <SearchSelect sort clearable value={String(tgt.connector ?? '')} onChange={(v) => setEndpoint('target', { connector: v })}
            options={connectorOptions(sqlConnectors)} placeholder="—" />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.schema')}</FieldLabel>
          <Input value={String(tgt.schema ?? '')} onChange={(e) => setEndpoint('target', { schema: e.target.value })} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.table')}</FieldLabel>
          <Input value={String(tgt.table ?? '')} onChange={(e) => setEndpoint('target', { table: e.target.value })} />
        </Field>
      </Grid>
      <SubHead>{t('nomaflow.steps.options')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.mode')}</FieldLabel>
          <SearchSelect value={String(f.mode ?? 'overwrite')} onChange={(v) => patch({ mode: v || 'overwrite' } as Partial<StepConfig>)}
            options={asOptions(['overwrite', 'append', 'upsert'])} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.typeCoercion')}</FieldLabel>
          <SearchSelect value={String(f.type_coercion ?? 'jde')} onChange={(v) => patch({ type_coercion: v || 'jde' } as Partial<StepConfig>)}
            options={asOptions(['jde', 'none'])} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.decimalMode')}</FieldLabel>
          <SearchSelect value={String(f.decimal_mode ?? 'truncate')} onChange={(v) => patch({ decimal_mode: v || 'truncate' } as Partial<StepConfig>)}
            options={asOptions(['truncate', 'preserve'])} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.batchSize')}</FieldLabel>
          <Input
            type="number" min={1}
            value={String(f.batch_size ?? 10000)}
            onChange={(e) => patch({ batch_size: Number(e.target.value) || 10000 } as Partial<StepConfig>)}
          />
        </Field>
        {/* Per-step, per-table list of column names that get full strip() (both ends) instead
            of the default rstrip when the source pool's trim_strings is on. Use for JDE-style
            right-justified codes left-padded with spaces (F0005 = DRKY/DRMCU; F0101 = ABKY).
            Matching is case-insensitive against the introspected column name.

            NB: we pass StringListEditor's emit straight through (no filter). An earlier version
            stripped empty strings here, which silently removed the blank row "Add" just created
            — the operator saw the button do nothing. Empty rows survive in state; the empty
            strings get dropped at save time below (where we strip the field entirely when the
            list is empty) and never reach the runner. */}
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.stripBothColumns')}</FieldLabel>
          <StringListEditor
            value={Array.isArray(f.strip_both_columns) ? (f.strip_both_columns as string[]) : []}
            onChange={(v) => patch({ strip_both_columns: v } as Partial<StepConfig>)}
          />
        </Field>
      </Grid>
    </>
  )
}

function SqlQueryForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const connName = String(f.connector ?? '')
  const conn = sqlConnectors.find((c) => c.name === connName)
  const queries = conn && conn.type === 'sql' ? conn.queries : []
  return (
    <Grid>
      <Field>
        <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
        <SearchSelect sort clearable value={connName} onChange={(v) => patch({ connector: v } as Partial<StepConfig>)}
          options={connectorOptions(sqlConnectors)} placeholder="—" />
      </Field>
      <Field $span={2}>
        <FieldLabel>{t('nomaflow.steps.query')}</FieldLabel>
        {queries.length > 0 ? (
          <SearchSelect sort clearable allowCustom value={String(f.query ?? '')} onChange={(v) => patch({ query: v } as Partial<StepConfig>)}
            options={queries.map((q) => ({ value: q.name, label: q.description || q.label || q.name, mono: q.name }))} placeholder="—" />
        ) : (
          <Input
            value={String(f.query ?? '')}
            onChange={(e) => patch({ query: e.target.value } as Partial<StepConfig>)}
            placeholder={t('nomaflow.steps.queryFreeText')}
          />
        )}
      </Field>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.params')}</FieldLabel>
        <KeyValueEditor
          value={(f.params ?? {}) as Record<string, unknown>}
          onChange={(v) => patch({ params: v } as Partial<StepConfig>)}
        />
      </Field>
    </Grid>
  )
}

// Module-level cache for the discovered callable catalog. Multiple python
// steps in the same job (and across job edits in the same session) reuse
// the same in-memory list — avoids hammering the backend on every step
// expansion. ``null`` = not yet fetched; ``[]`` = fetched + empty (no

function PythonForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  return (
    <Grid>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.callable')}</FieldLabel>
        <CallableSelect
          value={String(f.callable ?? '')}
          onChange={(v) => patch({ callable: v } as Partial<StepConfig>)}
          placeholder={t('nomaflow.steps.callablePlaceholder')}
        />
      </Field>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.opKwargs')}</FieldLabel>
        <KeyValueEditor
          value={(f.op_kwargs ?? {}) as Record<string, unknown>}
          onChange={(v) => patch({ op_kwargs: v } as Partial<StepConfig>)}
        />
      </Field>
    </Grid>
  )
}

// The installation's directory pools (an `ldap://` / `ldaps://` pool), asked once: the LDAP step names one
// instead of carrying a server and an account. Who may not read the pools still types the name.
let _directoryPools: Promise<SearchSelectOption[]> | null = null
const directoryPools = (): Promise<SearchSelectOption[]> => {
  _directoryPools ??= api.get<{ pools: Record<string, { url?: string }> }>('/admin/config/pools')
    .then((d) => Object.entries(d.pools ?? {})
      .filter(([, p]) => { const parts = urlParts(p.url ?? ''); return parts != null && isDirectory(parts.scheme) })
      .map(([name, p]) => ({ value: name, label: poolSummary(p.url ?? '').where || name, mono: name })))
    .catch(() => [])
  return _directoryPools
}

function LdapSyncForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const [pools, setPools] = useState<SearchSelectOption[]>([])
  useEffect(() => { let on = true; void directoryPools().then((o) => { if (on) setPools(o) }); return () => { on = false } }, [])
  const pooled = !!String(f.pool ?? '').trim()
  const text = (key: string, label: string, span?: number, placeholder?: string) => (
    <Field $span={span}>
      <FieldLabel>{label}</FieldLabel>
      <Input
        value={String(f[key] ?? '')}
        onChange={(e) => patch({ [key]: e.target.value } as Partial<StepConfig>)}
        placeholder={placeholder}
      />
    </Field>
  )
  return (
    <>
      <Grid>
        <Field $span={3}>
          <FieldLabel title={t('nomaflow.steps.directoryPoolHint')}>{t('nomaflow.steps.directoryPool')}</FieldLabel>
          <SearchSelect sort value={String(f.pool ?? '')} options={pools} allowCustom anyLabel={t('common.none')}
            onChange={(v) => patch({ pool: v || undefined } as Partial<StepConfig>)} />
          <div style={{ color: colors.text.muted, fontSize: fontSize.sm, marginTop: 4 }}>{t('nomaflow.steps.directoryPoolHint')}</div>
        </Field>
        {/* the step's own server and account: only while it names no pool */}
        {!pooled && text('server', t('nomaflow.steps.server'), 3, 'ldaps://ad.example.com')}
        {!pooled && text('bind_dn', t('nomaflow.steps.bindDn'), 3)}
        {!pooled && text('bind_password', t('nomaflow.steps.bindPassword'), 3)}
        {text('search_base', t('nomaflow.steps.searchBase'), 3)}
        {text('search_filter', t('nomaflow.steps.searchFilter'), 3)}
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.attributes')}</FieldLabel>
          <Input
            value={(Array.isArray(f.attributes) ? f.attributes : []).join(', ')}
            onChange={(e) => patch({
              attributes: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
            } as Partial<StepConfig>)}
            placeholder="sAMAccountName, mail, displayName"
          />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.targetConnector')}</FieldLabel>
          <SearchSelect sort clearable
            value={String(f.target_connector ?? '')}
            onChange={(v) => patch({ target_connector: v } as Partial<StepConfig>)}
            options={connectorOptions(sqlConnectors)} placeholder="—"
          />
        </Field>
        {text('target_query', t('nomaflow.steps.targetQuery'), 2)}
      </Grid>
      <SubHead>{t('nomaflow.steps.mapping')}</SubHead>
      <KeyValueEditor
        value={(f.mapping ?? {}) as Record<string, unknown>}
        onChange={(v) => patch({ mapping: v } as Partial<StepConfig>)}
      />
    </>
  )
}

function HttpForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  return (
    <>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.method')}</FieldLabel>
          <SearchSelect value={String(f.method ?? 'GET')} onChange={(v) => patch({ method: v || 'GET' } as Partial<StepConfig>)}
            options={asOptions(['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])} />
        </Field>
        <Field $span={2}>
          <FieldLabel>{t('nomaflow.steps.url')}</FieldLabel>
          <Input
            value={String(f.url ?? '')}
            onChange={(e) => patch({ url: e.target.value } as Partial<StepConfig>)}
            placeholder="https://api.example.com/hook"
          />
        </Field>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.headers')}</FieldLabel>
          <KeyValueEditor
            value={(f.headers ?? {}) as Record<string, unknown>}
            onChange={(v) => patch({ headers: v } as Partial<StepConfig>)}
          />
        </Field>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.body')}</FieldLabel>
          <TextArea
            value={typeof f.body === 'string' ? f.body : (f.body == null ? '' : JSON.stringify(f.body, null, 2))}
            onChange={(e) => patch({ body: e.target.value } as Partial<StepConfig>)}
          />
        </Field>
      </Grid>
    </>
  )
}

// ── call_job: pick another job from the catalog ───────────────────────────────────
//
// Reuses the same module-level cache pattern as the PythonForm callable
// catalog — one fetch per browser session, shared across all call_job steps.
// allowCustom stays on (an operator might be drafting a target job that
// doesn't exist yet — let them type the id; the validator at fire time
// surfaces the typo as a clear "target not found" error).
type JobBrief = { id: string; description?: string | null; tags?: string[] }
let _jobIdsCache: JobBrief[] | null = null
let _jobIdsPromise: Promise<JobBrief[]> | null = null
function loadJobIds(): Promise<JobBrief[]> {
  if (_jobIdsCache) return Promise.resolve(_jobIdsCache)
  if (_jobIdsPromise) return _jobIdsPromise
  _jobIdsPromise = api.get<{ jobs: JobBrief[] }>('/admin/jobs')
    .then((r) => { _jobIdsCache = r.jobs; return r.jobs })
    .finally(() => { _jobIdsPromise = null })
  return _jobIdsPromise
}


/** ``file_export``: a read query → an xlsx / csv file in the run's work area (``notify`` attaches
 *  it), optionally copied to a folder. */
function FileExportForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const connName = String(f.connector ?? '')
  const conn = sqlConnectors.find((c) => c.name === connName)
  const queries = conn && conn.type === 'sql' ? conn.queries.filter((q) => !q.writable) : []
  const connOpts: SearchSelectOption[] = sqlConnectors.map((c) => ({ value: c.name, label: c.name, mono: c.name }))
  const queryOpts: SearchSelectOption[] = queries.map((q) => ({ value: q.name, label: q.name, mono: q.name }))
  const fmtOpts: SearchSelectOption[] = [{ value: 'xlsx', label: 'Excel (xlsx)' }, { value: 'csv', label: 'CSV' }]
  const set = (k: string, v: unknown) => patch({ [k]: v } as Partial<StepConfig>)
  return (
    <>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
          <SearchSelect sort value={connName} onChange={(v) => set('connector', v)} options={connOpts} placeholder="—" />
        </Field>
        <Field $span={2}>
          <FieldLabel>{t('nomaflow.steps.query')}</FieldLabel>
          <SearchSelect sort allowCustom value={String(f.query ?? '')} onChange={(v) => set('query', v)} options={queryOpts} placeholder={t('nomaflow.steps.queryFreeText')} />
        </Field>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.params')}</FieldLabel>
          <KeyValueEditor value={(f.params ?? {}) as Record<string, unknown>} onChange={(v) => set('params', v)} />
        </Field>
      </Grid>
      <SubHead>{t('nomaflow.steps.file')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.exportFormat')}</FieldLabel>
          <SearchSelect value={String(f.format ?? 'xlsx')} onChange={(v) => set('format', v || 'xlsx')} options={fmtOpts} />
        </Field>
        <Field $span={2}>
          <FieldLabel>{t('nomaflow.steps.exportFilename')}</FieldLabel>
          <Input value={String(f.filename ?? '')} onChange={(e) => set('filename', e.target.value || undefined)} placeholder="sales-${params.period}" />
        </Field>
        {String(f.format ?? 'xlsx') === 'xlsx' ? (
          <Field>
            <FieldLabel>{t('nomaflow.steps.sheet')}</FieldLabel>
            <Input value={String(f.sheet ?? '')} onChange={(e) => set('sheet', e.target.value || undefined)} placeholder={String(f.query ?? '')} />
          </Field>
        ) : (
          <>
            <Field>
              <FieldLabel>{t('nomaflow.steps.delimiter')}</FieldLabel>
              <Input value={String(f.delimiter ?? '')} onChange={(e) => set('delimiter', e.target.value || undefined)} placeholder=";" />
            </Field>
            <Field>
              <FieldLabel>{t('nomaflow.steps.encoding')}</FieldLabel>
              <Input value={String(f.encoding ?? 'utf-8-sig')} onChange={(e) => set('encoding', e.target.value || 'utf-8-sig')} />
            </Field>
          </>
        )}
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.exportDir')}</FieldLabel>
          <Input value={String(f.export_dir ?? '')} onChange={(e) => set('export_dir', e.target.value || undefined)} placeholder="/data/out/${params.period}" />
        </Field>
      </Grid>
    </>
  )
}

function CallJobForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const [catalog, setCatalog] = useState<JobBrief[]>(_jobIdsCache ?? [])
  useEffect(() => {
    if (_jobIdsCache) return
    let cancelled = false
    loadJobIds().then((list) => { if (!cancelled) setCatalog(list) }).catch(() => { /* silent */ })
    return () => { cancelled = true }
  }, [])
  const options = useMemo<SearchSelectOption[]>(
    () => catalog.map((j) => ({
      value: j.id,
      mono: j.id,
      label: j.description || j.id,
    })),
    [catalog],
  )
  return (
    <Grid>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.targetJobId')}</FieldLabel>
        <SearchSelect sort
          value={String(f.target_job_id ?? '')}
          onChange={(v) => patch({ target_job_id: v } as Partial<StepConfig>)}
          options={options}
          allowCustom
          placeholder={t('nomaflow.steps.targetJobIdPlaceholder')}
          loading={catalog.length === 0 && _jobIdsPromise !== null}
        />
      </Field>
    </Grid>
  )
}

// ── one step card ──────────────────────────────────────────────────────────────────
function StepCard({ step, index, count, sqlConnectors, onPatch, turnedOff, onTurnedOff, onMove, onDuplicate, onDelete, alwaysOpen = false }: {
  /** Shown in the flow's side panel: no folding, its fields are there. */
  alwaysOpen?: boolean
  step: StepConfig
  index: number
  count: number
  sqlConnectors: ConnectorMeta[]
  onPatch: (p: Partial<StepConfig>) => void
  turnedOff?: boolean
  onTurnedOff?: (off: boolean) => void
  onMove: (dir: -1 | 1) => void
  onDuplicate: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const [folded, setOpen] = useState(false)
  const open = alwaysOpen || folded
  const Chev = open ? ChevronDown : ChevronRight
  return (
    <Card style={alwaysOpen ? { border: 'none', overflow: 'visible' } : undefined}>
      <Head onClick={() => { if (!alwaysOpen) setOpen((o) => !o) }} style={alwaysOpen ? { cursor: 'default', padding: '0 0 10px' } : undefined}>
        {!alwaysOpen && <Chev size={14} />}
        <Tag $tone={STEP_TONE[step.type] ?? 'neutral'}>{step.type}</Tag>
        <HeadName>{step.name || t('nomaflow.steps.unnamed')}</HeadName>
        <HeadSummary>{stepSummary(step)}</HeadSummary>
        {turnedOff && <Tag $tone="neutral">{t('nomaflow.steps.turnedOffTag')}</Tag>}
        <IconBtn type="button" disabled={index === 0} title={t('common.up')}
          onClick={(e) => { e.stopPropagation(); onMove(-1) }}><ArrowUp size={13} /></IconBtn>
        <IconBtn type="button" disabled={index === count - 1} title={t('common.down')}
          onClick={(e) => { e.stopPropagation(); onMove(1) }}><ArrowDown size={13} /></IconBtn>
        <IconBtn type="button" title={t('nomaflow.steps.duplicate')}
          onClick={(e) => { e.stopPropagation(); onDuplicate() }}><Copy size={13} /></IconBtn>
        <IconBtn type="button" title={t('common.delete')}
          onClick={(e) => { e.stopPropagation(); onDelete() }}><Trash2 size={13} /></IconBtn>
      </Head>
      {open && (
        <Body style={alwaysOpen ? { padding: 0, border: 'none' } : undefined}>
          <Field>
            <FieldLabel>{t('nomaflow.steps.name')}</FieldLabel>
            <Input value={step.name} onChange={(e) => onPatch({ name: e.target.value })} />
          </Field>
          <Checkbox checked={!!step.continue_on_failure} label={t('nomaflow.steps.continueOnFailure')}
            onChange={(c) => onPatch({ continue_on_failure: c || undefined })} />
          {onTurnedOff && (
            <Checkbox checked={!!turnedOff} label={t('nomaflow.steps.turnedOff')} onChange={(c) => onTurnedOff(c)} />
          )}
          {step.type === 'sql_copy' && <SqlCopyForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'sql_query' && <SqlQueryForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'python' && <PythonForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'ldap_sync' && <LdapSyncForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'http' && <HttpForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'call_job' && <CallJobForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'file_load' && <FileLoadForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'report' && <ReportForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'file_export' && <FileExportForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'notify' && <NotifyForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
        </Body>
      )}
    </Card>
  )
}

// ── report (C5): a saved explorer analysis / dashboard, or a plugin report, to a run file ──
const PERIOD_PRESETS = ['lastMonth', 'thisMonth', 'lastYear', 'thisYear', 'thisQuarter', 'last30', 'last7', 'yesterday', 'today', 'all']

function ReportForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const ref = String(f.report ?? 'analysis:')
  const [kind, , rest] = ref.includes(':') ? [ref.slice(0, ref.indexOf(':')), ':', ref.slice(ref.indexOf(':') + 1)] : ['analysis', ':', '']
  const app = String(f.app ?? ''), model = String(f.model ?? '')
  const [models, setModels] = useState<{ app: string; model: string; label: string }[]>([])
  const [saved, setSaved] = useState<{ id: string; name: string }[]>([])
  const [reports, setReports] = useState<{ scope: string; id: string; title: string; formats: string[] }[]>([])
  const [roles, setRoles] = useState<string[]>([])
  useEffect(() => {
    api.get<{ models: { app: string; model: string; label: string }[] }>('/api/explorer').then((r) => setModels(r.models)).catch(() => setModels([]))
    api.get<{ reports: { scope: string; id: string; title: string; formats: string[] }[] }>('/api/reports').then((r) => setReports(r.reports)).catch(() => setReports([]))
    api.get<{ roles: { name: string }[] }>('/admin/access/roles').then((r) => setRoles(r.roles.map((x) => x.name))).catch(() => setRoles([]))
  }, [])
  useEffect(() => {
    if (!app || !model || kind === 'report') { setSaved([]); return }
    api.get<{ items: { id: string; name: string; shared: boolean }[] }>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(model)}/saved/${kind === 'dashboard' ? 'dashboards' : 'analyses'}`)
      .then((r) => setSaved(r.items.filter((i) => i.shared))).catch(() => setSaved([]))
  }, [app, model, kind])
  const setRef = (k: string, id: string) => patch({ report: `${k}:${id}` } as Partial<StepConfig>)
  const kindOpts: SearchSelectOption[] = [
    { value: 'analysis', label: t('nomaflow.steps.reportAnalysis') }, { value: 'dashboard', label: t('nomaflow.steps.reportDashboard') }, { value: 'report', label: t('nomaflow.steps.reportPlugin') },
  ]
  const modelOpts: SearchSelectOption[] = models.map((m) => ({ value: `${m.app}/${m.model}`, label: m.label, mono: `${m.app} · ${m.model}` }))
  const periodOpts: SearchSelectOption[] = PERIOD_PRESETS.map((p) => ({ value: p, label: t(`period.${p}`) }))
  return (
    <>
      <SubHead>{t('nomaflow.steps.reportWhat')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.reportKind')}</FieldLabel>
          <SearchSelect value={kind} onChange={(v) => setRef(v, '')} options={kindOpts} />
        </Field>
        {kind !== 'report' ? (
          <>
            <Field>
              <FieldLabel>{t('nomaflow.steps.reportModel')}</FieldLabel>
              <SearchSelect sort value={app && model ? `${app}/${model}` : ''} options={modelOpts} placeholder={t('common.pick')}
                onChange={(v) => { const [a, m] = v.split('/'); patch({ app: a, model: m } as Partial<StepConfig>) }} />
            </Field>
            <Field>
              <FieldLabel>{kind === 'dashboard' ? t('explorer.dashboards') : t('explorer.analyses')}</FieldLabel>
              <SearchSelect sort value={rest} onChange={(v) => setRef(kind, v)} options={saved.map((s) => ({ value: s.id, label: s.name, mono: s.id }))} allowCustom placeholder={t('nomaflow.steps.reportSharedOnly')} />
            </Field>
          </>
        ) : (
          <Field $span={2}>
            <FieldLabel>{t('nomaflow.steps.reportPlugin')}</FieldLabel>
            <SearchSelect sort value={rest} onChange={(v) => setRef('report', v)} options={reports.map((r) => ({ value: `${r.scope}:${r.id}`, label: r.title, mono: `${r.scope}:${r.id}` }))} allowCustom />
          </Field>
        )}
        <Field>
          <FieldLabel>{t('nomaflow.steps.reportFormat')}</FieldLabel>
          <SearchSelect value={String(f.format ?? 'xlsx')} onChange={(v) => patch({ format: v } as Partial<StepConfig>)} options={[{ value: 'xlsx', label: 'Excel' }, { value: 'pdf', label: 'PDF' }]} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.reportPeriod')}</FieldLabel>
          <SearchSelect value={String(f.period ?? '')} onChange={(v) => patch({ period: v || undefined } as Partial<StepConfig>)} options={periodOpts} anyLabel={t('nomaflow.steps.reportPeriodOwn')} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.reportFilename')}</FieldLabel>
          <Input value={String(f.filename ?? '')} onChange={(e) => patch({ filename: e.target.value || undefined } as Partial<StepConfig>)} placeholder="monthly-sales" />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.reportAsUser')}</FieldLabel>
          <Input value={String(f.as_user ?? '')} onChange={(e) => patch({ as_user: e.target.value || undefined } as Partial<StepConfig>)} placeholder="cfo" />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.reportAsRole')}</FieldLabel>
          <SearchSelect sort value={String(f.as_role ?? '')} onChange={(v) => patch({ as_role: v || undefined } as Partial<StepConfig>)} options={roles.map((r) => ({ value: r, label: r }))} anyLabel={t('nomaflow.steps.reportUnrestricted')} allowCustom />
        </Field>
      </Grid>
    </>
  )
}

// ── notify (C5): the message, the recipients, the run's files attached ─────────────────
function NotifyForm({ step, patch }: FormProps) {
  const subjectRef = useRef<HTMLInputElement>(null)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const channels = (f.channels as string[] | undefined) ?? ['portal', 'email']
  const toggle = (c: string) => patch({ channels: channels.includes(c) ? channels.filter((x) => x !== c) : [...channels, c] } as Partial<StepConfig>)
  return (
    <>
      <SubHead>{t('nomaflow.steps.notifyMessage')}</SubHead>
      <Grid>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.notifyRecipients')}</FieldLabel>
          <StringListEditor value={(f.recipients as string[] | undefined) ?? []} onChange={(v) => patch({ recipients: v } as Partial<StepConfig>)} allowCustom />
        </Field>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.notifySubject')}</FieldLabel>
          <div style={{ display: 'flex', gap: 6 }}>
            <Input ref={subjectRef} value={String(f.subject ?? '')} onChange={(e) => patch({ subject: e.target.value } as Partial<StepConfig>)} placeholder="Sales digest {DATE}" />
            <PlaceholderPicker options={NOTIFY_STEP_TOKENS} value={String(f.subject ?? '')} onChange={(v) => patch({ subject: v } as Partial<StepConfig>)} targetRef={subjectRef} />
          </div>
        </Field>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.notifyBody')}</FieldLabel>
          <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
            <TextArea ref={bodyRef} value={typeof f.body === 'string' ? f.body : ''} onChange={(e) => patch({ body: e.target.value || undefined } as Partial<StepConfig>)} placeholder={t('nomaflow.steps.notifyBodyPlaceholder')} />
            <PlaceholderPicker options={NOTIFY_STEP_TOKENS} value={typeof f.body === 'string' ? f.body : ''} onChange={(v) => patch({ body: v || undefined } as Partial<StepConfig>)} targetRef={bodyRef} />
          </div>
        </Field>
        <Field $span={2}>
          <FieldLabel>{t('nomaflow.steps.notifyAttach')}</FieldLabel>
          <StringListEditor value={(f.attach as string[] | undefined) ?? ['*']} onChange={(v) => patch({ attach: v } as Partial<StepConfig>)} allowCustom />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.notifyChannels')}</FieldLabel>
          <div style={{ display: 'flex', gap: 12, paddingTop: 6 }}>
            {['portal', 'email'].map((c) => <Checkbox key={c} checked={channels.includes(c)} onChange={() => toggle(c)} label={c} />)}
          </div>
        </Field>
      </Grid>
    </>
  )
}

// ── the editor ─────────────────────────────────────────────────────────────────────
export default function StepEditor({ steps, onChange, connectors, turnedOff = [], onTurnedOff, settings }: {
  /** What the side panel shows while no step is picked (the flow's start): the job's own settings. */
  settings?: ReactNode
  steps: StepConfig[]
  onChange: (steps: StepConfig[]) => void
  connectors: ConnectorMeta[] | null
  /** The job's ``disabled_steps`` — steps turned off for this install. */
  turnedOff?: string[]
  onTurnedOff?: (names: string[]) => void
}) {
  const { t } = useTranslation()
  const sqlConnectors = useMemo(
    () => (connectors ?? []).filter((c): c is ConnectorMeta => c.type === 'sql'),
    [connectors],
  )

  const patchStep = (i: number, p: Partial<StepConfig>) =>
    onChange(steps.map((s, idx) => (idx === i ? { ...s, ...p } : s)))
  const moveStep = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= steps.length) return
    const next = [...steps]
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }
  const duplicateStep = (i: number) => {
    const clone: StepConfig = JSON.parse(JSON.stringify(steps[i]))
    clone.name = `${clone.name}_copy`
    onChange([...steps.slice(0, i + 1), clone, ...steps.slice(i + 1)])
  }
  const deleteStep = (i: number) => onChange(steps.filter((_, idx) => idx !== i))
  // the flow above the list: a card picks its step (opened and brought into view), a "+" inserts one at its place
  const [focus, setFocus] = useState<{ index: number; tick: number } | null>(null)
  const picked = focus && focus.index < steps.length ? focus.index : null
  const insertStep = (index: number, type: StepType) => {
    onChange([...steps.slice(0, index), blankStep(type), ...steps.slice(index)])
    setFocus({ index, tick: Date.now() })
  }

  return (
    <FlowSplit>
      <JobFlow
        steps={steps.map((s) => ({ name: s.name || t('nomaflow.steps.unnamed'), kind: s.type, tone: STEP_TONE[s.type] ?? 'blue', summary: stepSummary(s),
          off: turnedOff.includes(s.name), continues: false }))}
        kinds={STEP_TYPES} selected={picked}
        onStart={() => setFocus(null)}
        onSelect={(index) => setFocus({ index, tick: Date.now() })}
        onInsert={(index, kind) => insertStep(index, kind as StepType)}
      />
      <FlowSide>
        {picked != null ? (
          <StepCard
            key={picked}
            alwaysOpen
            step={steps[picked]}
            index={picked}
            count={steps.length}
            sqlConnectors={sqlConnectors}
            onPatch={(p) => patchStep(picked, p)}
            turnedOff={turnedOff.includes(steps[picked].name)}
            onTurnedOff={onTurnedOff && ((off) => onTurnedOff(off
              ? [...turnedOff.filter((n) => n !== steps[picked].name), steps[picked].name]
              : turnedOff.filter((n) => n !== steps[picked].name)))}
            onMove={(dir) => { moveStep(picked, dir); const j = picked + dir; if (j >= 0 && j < steps.length) setFocus({ index: j, tick: Date.now() }) }}
            onDuplicate={() => { duplicateStep(picked); setFocus({ index: picked + 1, tick: Date.now() }) }}
            onDelete={() => { deleteStep(picked); setFocus(null) }}
          />
        ) : settings}
      </FlowSide>
    </FlowSplit>
  )
}
