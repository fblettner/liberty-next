// Structured editor for `[pools.*]` — the first slice of the Phase-7 config builders. Lists the
// pools, renders each as a SchemaForm driven by PoolConfig's JSON Schema (GET /admin/config/schema),
// and on Save validates + surgically rewrites only the [pools.*] tables in connectors.toml
// (PUT /admin/config/pools) then reloads. No rename yet — delete + re-add. Renders the body only;
// Settings/index.tsx wraps the page.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, Database, Edit3, Undo2, GitBranch } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, SchemaForm, FrameworkEnumsContext, useModals, type FrameworkEnums, type JsonSchema } from '../../common'
import type { ConfigSchemas, PoolsDoc } from '../../types/config'
import { renameKey, validateRename } from '../../services/keyRename'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import { colors, fontSize, fonts, radius } from '../../theme'

type Pools = Record<string, Record<string, unknown>>

// Per-dialect starter URLs — picking a Dialect in the form seeds the URL field with the matching
// template (only when the URL is empty or still a template, so a real one the operator typed is
// kept). Dialect ids match the DATASOURCE_TYPE enum (liberty/framework_enums.py).
const URL_TEMPLATES: Record<string, string> = {
  postgresql: 'postgresql+asyncpg://user:password@host:5432/dbname',
  oracle: 'oracle+oracledb://user@host:1521/?service_name=ORCL',
  mysql: 'mysql+aiomysql://user:password@host:3306/dbname',
  mariadb: 'mariadb+asyncmy://user:password@host:3306/dbname',
  mssql: 'mssql+aioodbc://user:password@host:1433/dbname?driver=ODBC+Driver+18+for+SQL+Server',
  sqlite: 'sqlite+aiosqlite:///./database.db',
}
const TEMPLATE_SET = new Set(Object.values(URL_TEMPLATES))

// Layout: outer Stack flex-fills the page, the toolbar is fixed at top, the Split fills the
// remaining vertical space, and only the inner panels scroll. No page-level scroll — the
// operator sees Save / Reload / Add / Delete without chasing them past a long list.
const Shell = styled.div`
  display: flex; flex-direction: column; gap: 12px;
  flex: 1; min-height: 0; height: 100%;
`
const Toolbar = styled.div`
  display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;
`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const ToolbarDivider = styled.span`
  display: inline-block; width: 1px; height: 18px; background: ${colors.border}; margin: 0 2px;
`
const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: stretch;`
// Left nav scrolls on its own — pools rarely run into dozens, but a long list mustn't push
// the form panel off-screen. Add button moved to the top toolbar; this column is list-only.
const NavCol = styled.div`flex: 0 0 200px; display: flex; flex-direction: column; gap: 4px; min-height: 0;`
const NavList = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 4px;`
const NavItem = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 7px; padding: 7px 10px; border-radius: ${radius.md}; text-align: left;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.mono}; cursor: pointer;
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
// FormCol owns the vertical scroll for the selected record's form. Constrained to the Shell's
// remaining height so a tall SchemaForm scrolls *inside* the card instead of pushing the page
// down — the toolbar stays put, dirty / status / Save / Reload are always one click away.
const FormCol = styled(Card)`flex: 1; min-width: 0; min-height: 0; overflow-y: auto;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`

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

  const update = (name: string, v: Record<string, unknown>) => setPools((p) => ({ ...(p ?? {}), [name]: v }))
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
    const name = (await modals.prompt({
      title: t('settings.pools.add'),
      message: t('settings.pools.namePrompt'),
      placeholder: t('settings.pools.namePlaceholder', 'e.g. nomasx1, jdedwards'),
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
    const existing = Object.keys(pools)
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t('settings.pools.renamePrompt', { name: oldName }),
      defaultValue: oldName,
      submitLabel: t('settings.rename.button'),
      validate: (v) => {
        const err = validateRename(oldName, v, existing)
        if (err === 'unchanged') return null  // identity is a soft no-op (close as if cancelled)
        if (err === 'empty') return t('settings.rename.empty')
        if (err === 'exists') return t('settings.rename.exists', { name: v })
        return null
      },
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
  // Alphabetical sort — v1's per-row ``apps_seq`` ordering doesn't carry over, so we surface
  // pools in a stable name-sorted order. ``localeCompare`` handles mixed case naturally.
  const names = Object.keys(pools).sort((a, b) => a.localeCompare(b))

  return (
    <FrameworkEnumsContext.Provider value={enums}>
    <Shell>
      {/* Top toolbar — status on the left; doc-level actions (Add / Save / Discard) on the right.
          Per-pool actions (Rename / Reload from disk / Delete) live in the selected pool's pane, not
          here — they're contextual to the pool, not the whole document. */}
      <Toolbar>
        <ToolbarLeft>
          {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
          {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
          {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        </ToolbarLeft>
        <ToolbarRight>
          <Button $variant="ghost" $size="sm" onClick={addPool} disabled={busy}>
            <Plus size={13} /> {t('settings.pools.add')}
          </Button>
          <ToolbarDivider />
          <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
          </Button>
          <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty} title={t('common.discard')}>
            <Undo2 size={13} /> {t('common.discard')}
          </Button>
        </ToolbarRight>
      </Toolbar>
      <Split>
        <NavCol>
          <NavList>
            {names.map((n) => (
              <NavItem key={n} $active={n === sel} onClick={() => { setSel(n); setStatus(null) }}><Database size={13} /> {n}</NavItem>
            ))}
          </NavList>
        </NavCol>
        <FormCol>
          {sel && pools[sel] ? (
            <Stack gap={12}>
              {/* Per-pool actions live here, in the selected pool's pane — Rename / Reload from
                  disk / Delete all act on this pool (Delete persists immediately). */}
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>[pools.{sel}]</strong>
                <Row gap={6} style={{ alignItems: 'center' }}>
                  <Button $variant="ghost" $size="sm" onClick={() => setUsagesTarget({
                    kind: 'pool', name: sel, label: `pool ${sel}`,
                  })} disabled={busy}>
                    <GitBranch size={13} /> {t('findUsages.button', 'Find usages')}
                  </Button>
                  <Button $variant="ghost" $size="sm" onClick={() => renamePool(sel)} disabled={busy}>
                    <Edit3 size={13} /> {t('settings.rename.button')}
                  </Button>
                  <Button $variant="ghost" $size="sm" onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
                    {busy ? <SpinnerRing size={13} thickness={2} /> : <RefreshCw size={13} />} {t('settings.pools.reloadFromDisk')}
                  </Button>
                  <Button $variant="danger" $size="sm" onClick={() => removePool(sel)} disabled={busy} title={t('settings.pools.deleteOne', { name: sel })}>
                    <Trash2 size={13} /> {t('common.delete')}
                  </Button>
                </Row>
              </Row>
              <SchemaForm schema={schema} value={pools[sel]} onChange={(v) => onFormChange(sel, v)} />
            </Stack>
          ) : (
            <Empty>{names.length ? t('settings.pools.pickOne') : t('settings.pools.empty')}</Empty>
          )}
        </FormCol>
      </Split>
      {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
    </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
