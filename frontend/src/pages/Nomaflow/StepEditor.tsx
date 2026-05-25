// nomaflow step-pipeline editor (NOMAFLOW-UI.md §4) — the ETL-building surface.
// An ordered list of step cards: collapse/expand, up/down reorder, duplicate, delete;
// a per-type "+ Add" toolbar at the bottom.
//
// Increment 4 ships the hand-written sql_copy + sql_query forms (they need live
// connector/query dropdowns). python / ldap_sync / http get a name field + a
// raw-values fallback until increment 5 brings their SchemaForm.
import { useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, ArrowUp, ArrowDown, Trash2, Copy, Plus } from 'lucide-react'
import { Input, Select, Button, Tag, StringListEditor } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { ConnectorMeta } from '../../types/connectors'
import type { StepConfig, StepType } from './types'

// ── styled ───────────────────────────────────────────────────────────────────────
const List = styled.div`display: flex; flex-direction: column; gap: 8px;`
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
const IconBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: transparent;
  color: ${colors.text.muted}; cursor: pointer; flex-shrink: 0;
  &:hover:not(:disabled) { color: ${colors.text.primary}; background: ${colors.bg.card}; }
  &:disabled { opacity: 0.35; cursor: default; }
`
const Body = styled.div`padding: 12px; display: flex; flex-direction: column; gap: 12px;`
const Grid = styled.div`display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px;`
const Field = styled.label<{ $span?: number }>`
  display: flex; flex-direction: column; gap: 4px;
  grid-column: ${({ $span }) => ($span ? `span ${$span}` : 'auto')};
`
const FieldLabel = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const SubHead = styled.div`
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};
  text-transform: uppercase; letter-spacing: 0.04em;
`
const AddBar = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 4px;`
const Note = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const KvRow = styled.div`display: flex; gap: 6px; align-items: center;`
const TextArea = styled.textarea`
  width: 100%; min-height: 72px; padding: 8px 10px; resize: vertical;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input}; color: ${colors.text.primary};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; outline: none;
  &:focus { border-color: ${colors.blue.main}; }
`

const STEP_TONE: Record<StepType, 'blue' | 'green' | 'orange' | 'purple' | 'neutral'> = {
  sql_copy: 'blue', sql_query: 'green', python: 'purple', ldap_sync: 'orange', http: 'neutral',
}
const STEP_TYPES: StepType[] = ['sql_copy', 'sql_query', 'python', 'ldap_sync', 'http']

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
    default: return ''
  }
}

// ── small key/value editor (sql_query params, etc.) ────────────────────────────────
export function KeyValueEditor({ value, onChange }: {
  value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void
}) {
  const { t } = useTranslation()
  const entries = Object.entries(value ?? {})
  const setEntry = (i: number, k: string, v: string) => {
    const next = entries.map(([ek, ev], idx) => (idx === i ? [k, v] : [ek, ev])) as [string, unknown][]
    onChange(Object.fromEntries(next))
  }
  const removeEntry = (i: number) => onChange(Object.fromEntries(entries.filter((_, idx) => idx !== i)))
  const addEntry = () => onChange({ ...value, '': '' })
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {entries.map(([k, v], i) => (
        <KvRow key={i}>
          <Input placeholder="key" value={k} onChange={(e) => setEntry(i, e.target.value, String(v ?? ''))} />
          <Input placeholder="value" value={String(v ?? '')} onChange={(e) => setEntry(i, k, e.target.value)} />
          <IconBtn type="button" onClick={() => removeEntry(i)}><Trash2 size={13} /></IconBtn>
        </KvRow>
      ))}
      <Button type="button" $variant="ghost" $size="sm" onClick={addEntry} style={{ alignSelf: 'flex-start' }}>
        <Plus size={13} /> {t('nomaflow.steps.addParam')}
      </Button>
    </div>
  )
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
          <Select value={String(src.connector ?? '')} onChange={(e) => setEndpoint('source', { connector: e.target.value })}>
            <option value="">—</option>
            {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Select>
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
          <Select value={String(tgt.connector ?? '')} onChange={(e) => setEndpoint('target', { connector: e.target.value })}>
            <option value="">—</option>
            {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Select>
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
          <Select value={String(f.mode ?? 'overwrite')} onChange={(e) => patch({ mode: e.target.value } as Partial<StepConfig>)}>
            <option value="overwrite">overwrite</option>
            <option value="append">append</option>
            <option value="upsert">upsert</option>
          </Select>
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.typeCoercion')}</FieldLabel>
          <Select value={String(f.type_coercion ?? 'jde')} onChange={(e) => patch({ type_coercion: e.target.value } as Partial<StepConfig>)}>
            <option value="jde">jde</option>
            <option value="none">none</option>
          </Select>
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.decimalMode')}</FieldLabel>
          <Select value={String(f.decimal_mode ?? 'truncate')} onChange={(e) => patch({ decimal_mode: e.target.value } as Partial<StepConfig>)}>
            <option value="truncate">truncate</option>
            <option value="preserve">preserve</option>
          </Select>
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
          <FieldLabel>{t('nomaflow.steps.stripBothColumns', 'Full-strip columns (left + right padding)')}</FieldLabel>
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
        <Select value={connName} onChange={(e) => patch({ connector: e.target.value } as Partial<StepConfig>)}>
          <option value="">—</option>
          {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </Select>
      </Field>
      <Field $span={2}>
        <FieldLabel>{t('nomaflow.steps.query')}</FieldLabel>
        {queries.length > 0 ? (
          <Select value={String(f.query ?? '')} onChange={(e) => patch({ query: e.target.value } as Partial<StepConfig>)}>
            <option value="">—</option>
            {queries.map((q) => <option key={q.name} value={q.name}>{q.name}</option>)}
          </Select>
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

function PythonForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  return (
    <Grid>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.callable')}</FieldLabel>
        <Input
          value={String(f.callable ?? '')}
          onChange={(e) => patch({ callable: e.target.value } as Partial<StepConfig>)}
          placeholder="nomaflow.nomasx1.collect:run"
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

function LdapSyncForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
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
        {text('server', t('nomaflow.steps.server'), 3, 'ldaps://ad.example.com')}
        {text('bind_dn', t('nomaflow.steps.bindDn'), 3)}
        {text('bind_password', t('nomaflow.steps.bindPassword'), 3)}
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
          <Select
            value={String(f.target_connector ?? '')}
            onChange={(e) => patch({ target_connector: e.target.value } as Partial<StepConfig>)}
          >
            <option value="">—</option>
            {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Select>
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
          <Select value={String(f.method ?? 'GET')} onChange={(e) => patch({ method: e.target.value } as Partial<StepConfig>)}>
            {['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
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

// ── one step card ──────────────────────────────────────────────────────────────────
function StepCard({ step, index, count, sqlConnectors, onPatch, onMove, onDuplicate, onDelete }: {
  step: StepConfig
  index: number
  count: number
  sqlConnectors: ConnectorMeta[]
  onPatch: (p: Partial<StepConfig>) => void
  onMove: (dir: -1 | 1) => void
  onDuplicate: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const Chev = open ? ChevronDown : ChevronRight
  return (
    <Card>
      <Head onClick={() => setOpen((o) => !o)}>
        <Chev size={14} />
        <Tag $tone={STEP_TONE[step.type] ?? 'neutral'}>{step.type}</Tag>
        <HeadName>{step.name || t('nomaflow.steps.unnamed')}</HeadName>
        <HeadSummary>{stepSummary(step)}</HeadSummary>
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
        <Body>
          <Field>
            <FieldLabel>{t('nomaflow.steps.name')}</FieldLabel>
            <Input value={step.name} onChange={(e) => onPatch({ name: e.target.value })} />
          </Field>
          {step.type === 'sql_copy' && <SqlCopyForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'sql_query' && <SqlQueryForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'python' && <PythonForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'ldap_sync' && <LdapSyncForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'http' && <HttpForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
        </Body>
      )}
    </Card>
  )
}

// ── the editor ─────────────────────────────────────────────────────────────────────
export default function StepEditor({ steps, onChange, connectors }: {
  steps: StepConfig[]
  onChange: (steps: StepConfig[]) => void
  connectors: ConnectorMeta[] | null
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
  const addStep = (type: StepType) => onChange([...steps, blankStep(type)])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <List>
        {steps.map((step, i) => (
          <StepCard
            key={i}
            step={step}
            index={i}
            count={steps.length}
            sqlConnectors={sqlConnectors}
            onPatch={(p) => patchStep(i, p)}
            onMove={(dir) => moveStep(i, dir)}
            onDuplicate={() => duplicateStep(i)}
            onDelete={() => deleteStep(i)}
          />
        ))}
      </List>
      <AddBar>
        <Note>{t('nomaflow.steps.addLabel')}</Note>
        {STEP_TYPES.map((type) => (
          <Button key={type} type="button" $variant="ghost" $size="sm" onClick={() => addStep(type)}>
            <Plus size={13} /> {type}
          </Button>
        ))}
      </AddBar>
    </div>
  )
}
