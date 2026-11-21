// Liberty Next → Administer → Pools — the databases the installation connects to (superuser).
//
// A list of the pools on the left, each line saying what it connects to; the picked pool beside it, drawn as
// its stages — the database, the account, the schemas, the database links, the tuning — each card saying what
// it holds. The database's address is typed as what an operator knows (host, port, database or service), the
// SQLAlchemy URL composed from it and still there to edit; a URL the page cannot read part by part is shown
// alone and never rewritten (services/poolUrl.ts, tested). "Test connection" opens one connection with the
// pool as it is on the page, saved or not (POST /admin/config/pools/test). The other settings are the schema's
// own form, one group per stage.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, Database, Edit3, Undo2, GitBranch, PlugZap, KeyRound, Layers, Link2, SlidersHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import {
  Button, Banner, Centered, Card, FieldLabel, HeaderActions, Input, SpinnerRing, SchemaForm, FrameworkEnumsContext, useModals,
  type FrameworkEnums, type JsonSchema,
} from '../../common'
import type { ConfigSchemas, PoolsDoc } from '../../types/config'
import { renameKey } from '../../services/keyRename'
import { validateId } from '../../services/idValidator'
import {
  DIALECTS, composeUrl, dialectOf, optionOf, plainPassword, poolFigures, urlParts, withOption, type UrlParts,
} from '../../services/poolUrl'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import StageStrip, { type StageView } from './StageStrip'
import { colors, fontSize, fonts, radius } from '../../theme'
import { BuilderShell as Shell } from '../../common/builderShell'

type Pools = Record<string, Record<string, unknown>>
type PoolStage = 'database' | 'account' | 'schemas' | 'dblinks' | 'tuning'
interface TestResult { ok: boolean; dialect: string; duration_ms: number; version?: string; error?: string }

// a kind of database picked on an empty pool starts its URL from the kind's template (also the ones the page
// offered before — a URL still equal to one of them is a template nobody filled in)
const URL_TEMPLATES: Record<string, string> = Object.fromEntries(Object.entries(DIALECTS).map(([k, d]) => [k, d.template]))
const TEMPLATE_SET = new Set([
  ...Object.values(URL_TEMPLATES),
  'postgresql+asyncpg://user:password@host:5432/dbname', 'oracle+oracledb://user@host:1521/?service_name=ORCL',
  'mysql+aiomysql://user:password@host:3306/dbname', 'mariadb+asyncmy://user:password@host:3306/dbname',
  'mssql+aioodbc://user:password@host:1433/dbname?driver=ODBC+Driver+18+for+SQL+Server', 'sqlite+aiosqlite:///./database.db',
])
// the schema's groups (a Pydantic field's x_group; none = General) and the stage that shows each
const GROUP_OF: Record<'schemas' | 'dblinks' | 'tuning', string> = { schemas: 'Schemas', dblinks: 'DB Links', tuning: 'Pool' }

const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: stretch;`
const PoolList = styled(Card)`flex: 0 0 min(280px, 30%); min-width: 0; padding: 10px; display: flex; flex-direction: column; gap: 4px; overflow-y: auto;`
const PoolLine = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 9px; width: 100%; padding: 8px 10px; text-align: left; cursor: pointer; font-family: ${fonts.sans}; flex-shrink: 0;
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : colors.border)}; border-radius: ${radius.md};
  background: ${(p) => (p.$on ? colors.blue.bg : colors.bg.input)};
  & svg { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .where { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { border-color: ${(p) => (p.$on ? colors.blue.main : colors.blue.border)}; }
`
const Side = styled(Card)`flex: 1; min-width: 0; min-height: 0; padding: 0; display: flex; flex-direction: column; overflow: hidden;`
const SideHead = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input}; flex-wrap: wrap;
  & .name { font-family: ${fonts.mono}; font-weight: 600; color: ${colors.text.primary}; flex: 1; min-width: 120px; }
  & .actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
`
const SideBody = styled.div`flex: 1; min-height: 0; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 12px;`
const StageBody = styled.div`display: flex; flex-direction: column; gap: 12px; max-width: 1120px;`
// the address's parts on one grid: a label above, the input as wide as its column, the note under it
const Fields = styled.div`display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 12px 14px;`
const Cell = styled.div<{ $wide?: boolean }>`
  display: grid; grid-template-rows: subgrid; grid-row: span 3; row-gap: 4px; min-width: 0; grid-column: ${(p) => (p.$wide ? 'span 2' : 'auto')};
  & > label { align-self: end; margin: 0; }
  & > .help { font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans}; }
`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`

function Part({ label, help, wide, children }: { label: string; help?: string; wide?: boolean; children: ReactNode }) {
  return (
    <Cell $wide={wide}>
      <FieldLabel>{label}</FieldLabel>
      <div>{children}</div>
      <div className="help">{help ?? ''}</div>
    </Cell>
  )
}

export default function PoolsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const [schema, setSchema] = useState<JsonSchema | null>(null)
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
  const [pools, setPools] = useState<Pools | null>(null)
  const [original, setOriginal] = useState<string>('')   // JSON of the last-loaded pools, for the dirty check
  const [sel, setSel] = useState<string | null>(null)
  const [usagesTarget, setUsagesTarget] = useState<FindUsagesTarget | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState<PoolStage>('database')
  const [tests, setTests] = useState<Record<string, TestResult | 'running'>>({})

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([api.get<ConfigSchemas>('/admin/config/schema'), api.get<PoolsDoc>('/admin/config/pools')])
      .then(([s, d]) => {
        setSchema(s.pool); setEnums(s.framework_enums); setPools(d.pools); setOriginal(JSON.stringify(d.pools))
        setSel((cur) => (cur && d.pools[cur] ? cur : Object.keys(d.pools)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const dirty = useMemo(() => pools != null && JSON.stringify(pools) !== original, [pools, original])

  const update = (name: string, v: Record<string, unknown>) => {
    setPools((p) => ({ ...(p ?? {}), [name]: v }))
    setTests((r) => { const { [name]: _gone, ...rest } = r; return rest })   // an answer is about the settings it was asked with
    setStatus(null)
  }
  // When the operator changes the Dialect, seed the URL with that dialect's template — unless they've
  // already typed a real URL (we only overwrite an empty field or a still-untouched template).
  const onFormChange = (name: string, v: Record<string, unknown>) => {
    const prevDialect = typeof pools?.[name]?.dialect === 'string' ? (pools[name].dialect as string) : ''
    const nextDialect = typeof v.dialect === 'string' ? v.dialect : ''
    const url = typeof v.url === 'string' ? v.url.trim() : ''
    if (nextDialect && nextDialect !== prevDialect && URL_TEMPLATES[nextDialect] && (!url || TEMPLATE_SET.has(url))) {
      update(name, { ...v, url: URL_TEMPLATES[nextDialect] })
      return
    }
    update(name, v)
  }
  const addPool = async () => {
    const existing = pools ? Object.keys(pools) : []
    const name = (await modals.prompt({
      title: t('settings.pools.add'),
      message: t('settings.pools.namePrompt'),
      placeholder: t('settings.pools.namePlaceholder'),
      validate: (v) => validateId({ kind: 'pool', proposed: v, existing, mode: 'add' }),
    }))?.trim()
    if (!name) return
    if (pools && name in pools) { setSel(name); return }
    setPools((p) => ({ ...(p ?? {}), [name]: { url: '' } }))
    setSel(name); setStatus(null)
  }
  // Delete persists immediately (write + reload), not as a pending edit a discard/reload would
  // silently drop — same fix as the dictionary delete. Commits the current doc minus the pool;
  // a failure surfaces inline instead of the pool quietly reappearing.
  const removePool = async (name: string) => {
    const ok = await modals.confirm({
      title: t('settings.pools.delete'),
      message: t('settings.pools.confirmDelete', { name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const next = { ...(pools ?? {}) }; delete next[name]
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/pools', { pools: next })
      const r = await api.post<{ pools: string[] }>('/admin/reload')
      setSel((s) => (s === name ? null : s))
      setStatus(t('settings.pools.saved', { pools: r.pools.join(', ') || `(${t('common.none')})` }))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }
  // Discard local edits — revert to the last-loaded state (client-side, no network).
  const discard = () => {
    if (!original) return
    const orig = JSON.parse(original) as Pools
    setPools(orig)
    setSel((s) => (s && orig[s] ? s : Object.keys(orig)[0] ?? null))
    setStatus(null); setError(null)
  }
  // Rename the selected pool's dict key. Order-preserving (the renamed item stays in place in the
  // left nav). Validation: non-empty, not colliding with another existing pool — handled by the
  // modal's ``validate`` callback so errors render under the input instead of as separate alerts.
  // Cross-file refs (`[connectors.<X>] pool = "<old>"` in connectors.toml) are **not** auto-updated
  // — that's a separate file, owned by a different PUT endpoint; doing a multi-document write
  // here would need partial-failure handling. A hint at the bottom of the builder reminds.
  const renamePool = async (oldName: string) => {
    if (!pools) return
    const existing = Object.keys(pools).filter((k) => k !== oldName)
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t('settings.pools.renamePrompt', { name: oldName }),
      defaultValue: oldName,
      submitLabel: t('settings.rename.button'),
      validate: (v) => validateId({ kind: 'pool', proposed: v, existing, mode: 'rename', currentName: oldName }),
    }))?.trim()
    if (!next || next === oldName) return
    setPools((p) => renameKey(p ?? {}, oldName, next))
    setSel(next); setStatus(null)
  }

  async function save() {
    if (!pools) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/pools', { pools })
      const r = await api.post<{ pools: string[] }>('/admin/reload')
      setStatus(t('settings.pools.saved', { pools: r.pools.join(', ') || `(${t('common.none')})` }))
      load()  // re-fetch the normalised file (defaults dropped) so the form reflects what's on disk
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (error && !pools) return <Banner $tone="error">{error}</Banner>
  if (!pools || !schema) return <Centered />
  const names = Object.keys(pools).sort((a, b) => a.localeCompare(b))
  const pool = sel ? pools[sel] : undefined

  const testPool = async (name: string) => {
    setTests((r) => ({ ...r, [name]: 'running' }))
    try {
      const res = await api.post<TestResult>('/admin/config/pools/test', { pool: pools[name] })
      setTests((r) => ({ ...r, [name]: res }))
    } catch (e) {
      setTests((r) => ({ ...r, [name]: { ok: false, dialect: '', duration_ms: 0, error: e instanceof ApiError ? e.message : String(e) } }))
    }
  }

  // the schema's fields by group, to show one group — or one field — of the schema's own form
  const allFields = Object.keys(schema.properties ?? {})
  const groupOf = (k: string): string => schema.properties?.[k]?.x_group ?? 'General'
  const allGroups = [...new Set(allFields.map(groupOf))]
  const formOf = (name: string, group: string, only?: string[]) => (
    <SchemaForm
      key={`${name}:${group}:${(only ?? []).join()}`}
      schema={schema} value={pools[name]} onChange={(v) => onFormChange(name, v)}
      hiddenGroups={allGroups.filter((g) => g !== group)}
      hiddenFields={only ? allFields.filter((k) => !only.includes(k)) : undefined}
    />
  )

  const body = (name: string, value: Record<string, unknown>): ReactNode => {
    const url = typeof value.url === 'string' ? value.url : ''
    const parts = urlParts(url)
    const kind = parts ? dialectOf(parts.scheme) : ''
    const look = DIALECTS[kind]
    const setParts = (patch: Partial<UrlParts>) => { if (parts) update(name, { ...value, url: composeUrl({ ...parts, ...patch }) }) }
    if (stage === 'database') return (
      <StageBody>
        {formOf(name, 'General', ['dialect'])}
        {parts && (
          <Fields>
            {kind !== 'sqlite' && (
              <>
                <Part label={t('settings.pools.host')}><Input value={parts.host} data-part="host" onChange={(e) => setParts({ host: e.target.value })} /></Part>
                <Part label={t('settings.pools.port')}><Input value={parts.port} data-part="port" placeholder={look?.port} onChange={(e) => setParts({ port: e.target.value })} /></Part>
              </>
            )}
            <Part label={t(`settings.pools.${look?.database ?? 'database'}`)} help={kind === 'oracle' ? t('settings.pools.sidHelp') : undefined}>
              <Input value={parts.database ?? ''} data-part="database" onChange={(e) => setParts({ database: e.target.value })} style={{ fontFamily: fonts.mono }} />
            </Part>
            {kind === 'oracle' && (
              <Part label={t('settings.pools.serviceName')}>
                <Input value={optionOf(parts.options, 'service_name')} data-part="service_name" style={{ fontFamily: fonts.mono }}
                  onChange={(e) => { const options = withOption(parts.options, 'service_name', e.target.value); setParts({ options, database: parts.database ?? (options != null ? '' : null) }) }} />
              </Part>
            )}
            <Part label={t('settings.pools.driver')} help={t('settings.pools.driverHelp')}>
              <Input value={parts.scheme} data-part="scheme" onChange={(e) => setParts({ scheme: e.target.value.trim() || parts.scheme })} style={{ fontFamily: fonts.mono }} />
            </Part>
            <Part label={t('settings.pools.options')} help={t('settings.pools.optionsHelp')} wide>
              <Input value={parts.options ?? ''} data-part="options" onChange={(e) => setParts({ options: e.target.value === '' ? null : e.target.value })} style={{ fontFamily: fonts.mono }} />
            </Part>
          </Fields>
        )}
        {!parts && url && <Banner $tone="info">{t('settings.pools.urlAsIs')}</Banner>}
        <div>
          <FieldLabel>{t('settings.pools.url')}</FieldLabel>
          <Input value={url} data-part="url" onChange={(e) => update(name, { ...value, url: e.target.value })} style={{ fontFamily: fonts.mono }} />
          <Hint style={{ marginTop: 4 }}>{parts ? t('settings.pools.urlHelp') : t('settings.pools.urlEmptyHelp')}</Hint>
        </div>
      </StageBody>
    )
    if (stage === 'account') return (
      <StageBody>
        {parts && kind !== 'sqlite' && (
          <Fields>
            <Part label={t('settings.pools.user')}><Input value={parts.user} data-part="user" onChange={(e) => setParts({ user: e.target.value })} /></Part>
          </Fields>
        )}
        {parts?.password != null && (
          <Banner $tone="warning">
            {t('settings.pools.passwordInUrl')}{' '}
            <Button $variant="ghost" $size="sm" style={{ marginLeft: 8 }}
              onClick={() => update(name, { ...value, password: plainPassword(parts.password ?? ''), url: composeUrl({ ...parts, password: null }) })}>
              <KeyRound size={13} /> {t('settings.pools.movePassword')}
            </Button>
          </Banner>
        )}
        {formOf(name, 'General', ['password'])}
      </StageBody>
    )
    return <StageBody>{formOf(name, GROUP_OF[stage])}</StageBody>
  }

  const stagesOf = (value: Record<string, unknown>): StageView[] => {
    const f = poolFigures(value)
    return [
      { id: 'database', tone: 'blue', icon: <Database size={15} />, title: t('settings.pools.stage.database'),
        what: f.where ? `${f.dialect} · ${f.where}` : t('settings.pools.noUrl'), more: f.scheme, warn: !value.url },
      { id: 'account', tone: 'blue', icon: <KeyRound size={15} />, title: t('settings.pools.stage.account'),
        what: f.user || (f.dialect === 'sqlite' ? t('settings.pools.noAccount') : t('settings.pools.noUser')),
        more: f.passwordInUrl ? t('settings.pools.passwordInUrlShort') : f.passwordSet ? t('settings.pools.passwordSet') : t('settings.pools.noPassword'), warn: f.passwordInUrl },
      { id: 'schemas', tone: 'green', icon: <Layers size={15} />, title: t('settings.pools.stage.schemas'),
        what: t('settings.pools.schemasCount', { n: f.schemas.length }), more: f.schemas.slice(0, 6).join(' · ') },
      { id: 'dblinks', tone: 'green', icon: <Link2 size={15} />, title: t('settings.pools.stage.dblinks'),
        what: t('settings.pools.dblinksCount', { n: f.dblinks.length }), more: f.dblinks.slice(0, 6).join(' · ') },
      { id: 'tuning', tone: 'purple', icon: <SlidersHorizontal size={15} />, title: t('settings.pools.stage.tuning'),
        what: t('settings.pools.connections', { size: f.size, overflow: f.overflow }), more: f.flags.join(' · ') },
    ]
  }
  const test = sel ? tests[sel] : undefined

  return (
    <FrameworkEnumsContext.Provider value={enums}>
    <Shell>
      {/* Add / Discard / Save are in the page header, before Reload config */}
      <HeaderActions>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        <Button $variant="ghost" $size="sm" onClick={addPool} disabled={busy}>
          <Plus size={13} /> {t('settings.pools.add')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty}>
          <Undo2 size={13} /> {t('common.discard')}
        </Button>
        <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
        </Button>
      </HeaderActions>
      {status && <Banner $tone="ok">{status}</Banner>}
      {error && <Banner $tone="error">{error}</Banner>}
      <Split>
        <PoolList>
          {names.map((n) => {
            const f = poolFigures(pools[n])
            return (
              <PoolLine key={n} type="button" $on={n === sel} data-pool={n} onClick={() => { setSel(n); setStatus(null) }}>
                <Database size={14} />
                <span className="text">
                  <span className="name">{n}</span>
                  <span className="where">{f.where ? `${f.dialect} · ${f.where}` : t('settings.pools.noUrl')}</span>
                </span>
              </PoolLine>
            )
          })}
          {names.length === 0 && <Empty>{t('settings.pools.empty')}</Empty>}
        </PoolList>
        <Side>
          {sel && pool ? (
            <>
              <SideHead>
                <span className="name">{sel}</span>
                <span className="actions">
                  <Button $variant="primary" $size="sm" data-action="test" onClick={() => void testPool(sel)} disabled={busy || test === 'running' || !pool.url}>
                    {test === 'running' ? <SpinnerRing size={13} thickness={2} /> : <PlugZap size={13} />} {t('settings.pools.test')}
                  </Button>
                  <Button $variant="ghost" $size="sm" onClick={() => setUsagesTarget({ kind: 'pool', name: sel, label: `pool ${sel}` })} disabled={busy}>
                    <GitBranch size={13} /> {t('findUsages.button')}
                  </Button>
                  <Button $variant="ghost" $size="sm" onClick={() => renamePool(sel)} disabled={busy}>
                    <Edit3 size={13} /> {t('settings.rename.button')}
                  </Button>
                  <Button $variant="ghost" $size="sm" onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
                    <RefreshCw size={13} /> {t('settings.pools.reloadFromDisk')}
                  </Button>
                  <Button $variant="danger" $size="sm" onClick={() => removePool(sel)} disabled={busy} title={t('settings.pools.deleteOne', { name: sel })}>
                    <Trash2 size={13} /> {t('common.delete')}
                  </Button>
                </span>
              </SideHead>
              <SideBody>
                {test && test !== 'running' && (
                  <Banner $tone={test.ok ? 'ok' : 'error'} data-test={test.ok ? 'ok' : 'failed'}>
                    {test.ok
                      ? t('settings.pools.testOk', { ms: Math.round(test.duration_ms), what: [test.dialect, test.version].filter(Boolean).join(' ') })
                      : t('settings.pools.testFailed', { error: test.error ?? '' })}
                  </Banner>
                )}
                <StageStrip stages={stagesOf(pool)} current={stage} onPick={(id) => setStage(id as PoolStage)} />
                <div data-pool-body={stage}>{body(sel, pool)}</div>
              </SideBody>
            </>
          ) : (
            <Empty>{names.length ? t('settings.pools.pickOne') : t('settings.pools.empty')}</Empty>
          )}
        </Side>
      </Split>
      {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
    </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
