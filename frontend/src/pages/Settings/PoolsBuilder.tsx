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
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, Mono, SchemaForm, FrameworkEnumsContext, useModals, type FrameworkEnums, type JsonSchema } from '../../common'
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
  const modals = useModals()
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
  const removePool = async (name: string) => {
    const ok = await modals.confirm({
      title: t('settings.pools.delete'),
      message: t('settings.pools.confirmDelete', { name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    setPools((p) => { const next = { ...(p ?? {}) }; delete next[name]; return next })
    setSel((s) => (s === name ? null : s)); setStatus(null)
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
    <Stack gap={12}>
      {/* Top toolbar — config path on the left, scope-level actions on the right. The "Add pool"
          button used to sit at the bottom of the left nav; promoting it here keeps every builder
          consistent (Pools / Screens / Dictionary all expose "Add scope-item" + "Delete current"
          in one place) and makes the action discoverable without scrolling past a long list. */}
      <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <Mono>{path}</Mono>
        <Row gap={6}>
          <Button $variant="ghost" $size="sm" onClick={addPool} disabled={busy}>
            <Plus size={13} /> {t('settings.pools.add')}
          </Button>
          {sel && pools[sel] && (
            <Button $variant="danger" $size="sm" onClick={() => removePool(sel)} disabled={busy} title={t('settings.pools.deleteOne', { name: sel })}>
              <Trash2 size={13} /> {t('settings.pools.deleteOne', { name: sel })}
            </Button>
          )}
        </Row>
      </Row>
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
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>[pools.{sel}]</strong>
                <Button $variant="ghost" $size="sm" onClick={() => renamePool(sel)} disabled={busy}>
                  <Edit3 size={13} /> {t('settings.rename.button')}
                </Button>
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
