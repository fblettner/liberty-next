// Structured editor for `[pools.*]` — the first slice of the Phase-7 config builders. Lists the
// pools, renders each as a SchemaForm driven by PoolConfig's JSON Schema (GET /admin/config/schema),
// and on Save validates + surgically rewrites only the [pools.*] tables in connectors.toml
// (PUT /admin/config/pools) then reloads. No rename yet — delete + re-add. Renders the body only;
// Settings/index.tsx wraps the page.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, Database, Edit3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, Mono, SchemaForm, FrameworkEnumsContext, type FrameworkEnums, type JsonSchema } from '../../common'
import type { ConfigSchemas, PoolsDoc } from '../../types/config'
import { renameKey, validateRename } from '../../services/keyRename'
import { colors, fontSize, fonts, radius } from '../../theme'

type Pools = Record<string, Record<string, unknown>>

const Split = styled.div`display: flex; gap: 14px; align-items: flex-start;`
// Left nav scrolls on its own — pools rarely run into dozens, but the cap keeps the page wheel-
// friendly when a deployment does. Add button stays pinned outside the scroller.
const NavCol = styled.div`flex: 0 0 200px; display: flex; flex-direction: column; gap: 4px; max-height: calc(100dvh - 18rem);`
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
const FormCol = styled(Card)`flex: 1; min-width: 0;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`
const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.5; margin: 0;`

export default function PoolsBuilder() {
  const { t } = useTranslation()
  const [schema, setSchema] = useState<JsonSchema | null>(null)
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
  const [path, setPath] = useState('')
  const [pools, setPools] = useState<Pools | null>(null)
  const [original, setOriginal] = useState<string>('')   // JSON of the last-loaded pools, for the dirty check
  const [sel, setSel] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([api.get<ConfigSchemas>('/admin/config/schema'), api.get<PoolsDoc>('/admin/config/pools')])
      .then(([s, d]) => {
        setSchema(s.pool); setEnums(s.framework_enums); setPath(d.path); setPools(d.pools); setOriginal(JSON.stringify(d.pools))
        setSel((cur) => (cur && d.pools[cur] ? cur : Object.keys(d.pools)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const dirty = useMemo(() => pools != null && JSON.stringify(pools) !== original, [pools, original])

  const update = (name: string, v: Record<string, unknown>) => setPools((p) => ({ ...(p ?? {}), [name]: v }))
  const addPool = () => {
    const name = window.prompt(t('settings.pools.namePrompt'))?.trim()
    if (!name) return
    if (pools && name in pools) { setSel(name); return }
    setPools((p) => ({ ...(p ?? {}), [name]: { url: '' } }))
    setSel(name); setStatus(null)
  }
  const removePool = (name: string) => {
    if (!window.confirm(t('settings.pools.confirmDelete', { name }))) return
    setPools((p) => { const next = { ...(p ?? {}) }; delete next[name]; return next })
    setSel((s) => (s === name ? null : s)); setStatus(null)
  }
  // Rename the selected pool's dict key. Order-preserving (the renamed item stays in place in the
  // left nav). Validation: non-empty, not colliding with another existing pool. Cross-file refs
  // (`[connectors.<X>] pool = "<old>"` in connectors.toml) are **not** auto-updated — that's a
  // separate file, owned by a different PUT endpoint; doing a multi-document write here would
  // need partial-failure handling. A hint at the bottom of the builder reminds the operator.
  const renamePool = (oldName: string) => {
    if (!pools) return
    const next = window.prompt(t('settings.pools.renamePrompt', { name: oldName }), oldName)?.trim()
    if (!next) return
    const err = validateRename(oldName, next, Object.keys(pools))
    if (err === 'unchanged') return
    if (err === 'empty') { window.alert(t('settings.rename.empty')); return }
    if (err === 'exists') { window.alert(t('settings.rename.exists', { name: next })); return }
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
    <Stack gap={12}>
      <Mono>{path}</Mono>
      <Split>
        <NavCol>
          <NavList>
            {names.map((n) => (
              <NavItem key={n} $active={n === sel} onClick={() => { setSel(n); setStatus(null) }}><Database size={13} /> {n}</NavItem>
            ))}
          </NavList>
          <Button $variant="ghost" $size="sm" onClick={addPool} style={{ marginTop: 6, justifyContent: 'flex-start' }}>
            <Plus size={13} /> {t('settings.pools.add')}
          </Button>
        </NavCol>
        <FormCol>
          {sel && pools[sel] ? (
            <Stack gap={12}>
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>[pools.{sel}]</strong>
                <Row gap={6}>
                  <Button $variant="ghost" $size="sm" onClick={() => renamePool(sel)} disabled={busy}>
                    <Edit3 size={13} /> {t('settings.rename.button')}
                  </Button>
                  <Button $variant="danger" $size="sm" onClick={() => removePool(sel)} disabled={busy}>
                    <Trash2 size={13} /> {t('settings.pools.delete')}
                  </Button>
                </Row>
              </Row>
              <SchemaForm schema={schema} value={pools[sel]} onChange={(v) => update(sel, v)} />
            </Stack>
          ) : (
            <Empty>{names.length ? t('settings.pools.pickOne') : t('settings.pools.empty')}</Empty>
          )}
        </FormCol>
      </Split>
      <Row>
        <Button $variant="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={14} thickness={2} /> : <Save size={14} />} {t('common.save')}
        </Button>
        <Button onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
          {busy ? <SpinnerRing size={14} thickness={2} /> : <RefreshCw size={14} />} {t('settings.pools.reloadFromDisk')}
        </Button>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
      </Row>
      <Hint>{t('settings.pools.hint')}</Hint>
    </Stack>
    </FrameworkEnumsContext.Provider>
  )
}
