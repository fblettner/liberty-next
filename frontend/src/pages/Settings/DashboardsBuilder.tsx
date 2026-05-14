// Structured editor for `config/dashboards.toml` — the Phase-8 dashboard catalog. Layout: left
// column lists one chip per `[dashboards.<id>]`, right column is a SchemaNavigator over the
// selected Dashboard. The navigator handles widgets / filters drill-in (`[[widgets]]` and
// `[[filters]]` arrays render as drill-in lists; clicking an item descends into its
// ChartWidget / KpiWidget / DashboardFilter sub-form), so we don't need any custom tree UI.
//
// Save validates against DashboardsFile + rewrites `dashboards.toml` via tomlkit (the
// `[dashboards]` table replaced wholesale — nested widgets/filters round-trip cleanly), then
// reloads. No rename of a dashboard's key yet (delete + re-add). The inspector lets you rename
// an entry's `id` and the dict key follows.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, LayoutDashboard } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import {
  Banner,
  Button,
  Card,
  Centered,
  FrameworkEnumsContext,
  Mono,
  Row,
  SchemaNavigator,
  SpinnerRing,
  Stack,
  type FrameworkEnums,
  type JsonSchema,
} from '../../common'
import type { ConfigSchemas, DashboardsDoc } from '../../types/config'
import { colors, fontSize, fonts, radius } from '../../theme'

type Dashboards = Record<string, Record<string, unknown>>

const Split = styled.div`display: flex; gap: 14px; align-items: flex-start;`
const NavCol = styled.div`flex: 0 0 220px; display: flex; flex-direction: column; gap: 4px; max-height: calc(100dvh - 18rem);`
const NavList = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 4px;`
const NavItem = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: ${radius.md}; text-align: left;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  cursor: pointer; min-width: 0;
  & > svg { flex-shrink: 0; color: ${colors.text.muted}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .lbl {
    font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted};
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const FormCol = styled(Card)`flex: 1; min-width: 0;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`
const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.5; margin: 0;`

export default function DashboardsBuilder() {
  const { t } = useTranslation()
  // We navigate the `Dashboard` $def with the full $defs map alongside so SchemaNavigator can
  // resolve widget / filter refs into ChartWidget / KpiWidget / DashboardFilter at the right
  // level when the user drills in.
  const [dashboardSchema, setDashboardSchema] = useState<JsonSchema | null>(null)
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
  const [path, setPath] = useState('')
  const [doc, setDoc] = useState<Dashboards | null>(null)
  const [original, setOriginal] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<DashboardsDoc>('/admin/config/dashboards/parsed'),
    ])
      .then(([s, d]) => {
        // Lift the Dashboard $def to the top + thread the whole $defs so SchemaNavigator
        // resolves ChartWidget / KpiWidget / DashboardFilter when the user drills in.
        const defs = (s.dashboards.$defs ?? {}) as Record<string, JsonSchema>
        const dashboard = (defs.Dashboard ?? {}) as JsonSchema
        setDashboardSchema({ ...dashboard, $defs: defs })
        setEnums(s.framework_enums)
        setPath(d.path); setDoc(d.dashboards); setOriginal(JSON.stringify(d.dashboards))
        setSel((cur) => (cur && d.dashboards[cur] ? cur : Object.keys(d.dashboards)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const dirty = useMemo(() => doc != null && JSON.stringify(doc) !== original, [doc, original])

  const update = (id: string, v: Record<string, unknown>) =>
    setDoc((p) => {
      // SchemaNavigator may have renamed `id` — the dict key follows so the on-disk shape stays
      // consistent (the parser injects `id` from the key, so a mismatch fails validation).
      const cur = p ?? {}
      const newId = typeof v.id === 'string' && v.id.trim() ? v.id.trim() : id
      const next = { ...cur }
      if (newId !== id) {
        delete next[id]
        setSel(newId)
      }
      next[newId] = { ...v, id: newId }
      return next
    })

  const addDashboard = () => {
    const id = window.prompt(t('settings.dashboards.namePrompt'))?.trim()
    if (!id) return
    if (doc && id in doc) { setSel(id); return }
    setDoc((p) => ({ ...(p ?? {}), [id]: { id, label: id, widgets: [] } }))
    setSel(id); setStatus(null)
  }
  const removeDashboard = (id: string) => {
    if (!window.confirm(t('settings.dashboards.confirmDelete', { name: id }))) return
    setDoc((p) => { const next = { ...(p ?? {}) }; delete next[id]; return next })
    setSel((s) => (s === id ? null : s)); setStatus(null)
  }

  async function save() {
    if (!doc) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/dashboards/parsed', { dashboards: doc })
      const r = await api.post<{ dashboards: string[] }>('/admin/reload')
      const list = (r.dashboards ?? []).join(', ') || `(${t('common.none')})`
      setStatus(t('settings.dashboards.saved', { dashboards: list }))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc || !dashboardSchema) return <Centered />
  const ids = Object.keys(doc)
  const selValue = sel ? doc[sel] : null

  return (
    <FrameworkEnumsContext.Provider value={enums}>
      <Stack gap={12}>
        <Mono>{path}</Mono>
        <Split>
          <NavCol>
            <NavList>
              {ids.map((id) => {
                const dash = doc[id] as { label?: string; description?: string | null }
                return (
                  <NavItem key={id} $active={id === sel} onClick={() => { setSel(id); setStatus(null) }}>
                    <LayoutDashboard size={13} />
                    <span className="text">
                      <span className="name">{id}</span>
                      {(dash.label || dash.description) && (
                        <span className="lbl">{dash.label || dash.description}</span>
                      )}
                    </span>
                  </NavItem>
                )
              })}
              {ids.length === 0 && (
                <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '2px 4px' }}>
                  {t('settings.dashboards.empty')}
                </div>
              )}
            </NavList>
            <Button $variant="ghost" $size="sm" onClick={addDashboard} style={{ marginTop: 6, justifyContent: 'flex-start' }}>
              <Plus size={13} /> {t('settings.dashboards.add')}
            </Button>
          </NavCol>
          <FormCol>
            {sel && selValue ? (
              <Stack gap={12}>
                <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                    [dashboards.{sel}]
                  </strong>
                  <Button $variant="danger" $size="sm" onClick={() => removeDashboard(sel)} disabled={busy}>
                    <Trash2 size={13} /> {t('settings.dashboards.delete')}
                  </Button>
                </Row>
                <SchemaNavigator
                  root={{
                    label: sel,
                    schema: dashboardSchema,
                    value: selValue,
                    onChange: (v) => update(sel, v),
                  }}
                />
              </Stack>
            ) : (
              <Empty>{ids.length ? t('settings.dashboards.pickOne') : t('settings.dashboards.empty')}</Empty>
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
        <Hint>{t('settings.dashboards.hint')}</Hint>
      </Stack>
    </FrameworkEnumsContext.Provider>
  )
}
