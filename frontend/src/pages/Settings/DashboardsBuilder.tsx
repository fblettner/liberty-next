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
  useModals,
  type FrameworkEnums,
  type JsonSchema,
} from '../../common'
import type { ChartsDoc, ConfigSchemas, DashboardsDoc } from '../../types/config'
import { colors, fontSize, fonts, radius } from '../../theme'

type Dashboards = Record<string, Record<string, unknown>>

// Layout: outer Shell flex-fills, top toolbar is fixed, the Split fills remaining height,
// only the inner panels scroll. Same pattern as PoolsBuilder.
const Shell = styled.div`
  display: flex; flex-direction: column; gap: 12px;
  flex: 1; min-height: 0; height: 100%;
`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const ToolbarDivider = styled.span`
  display: inline-block; width: 1px; height: 18px; background: ${colors.border}; margin: 0 2px;
`
const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: stretch;`
const NavCol = styled.div`flex: 0 0 220px; display: flex; flex-direction: column; gap: 4px; min-height: 0;`
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
const FormCol = styled(Card)`flex: 1; min-width: 0; min-height: 0; overflow-y: auto;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`

export default function DashboardsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  // We navigate the `Dashboard` $def with the full $defs map alongside so SchemaNavigator can
  // resolve widget / filter refs into ChartWidget / KpiWidget / DashboardFilter at the right
  // level when the user drills in.
  const [dashboardSchema, setDashboardSchema] = useState<JsonSchema | null>(null)
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
  // Saved charts catalog — drives the ChartWidget.chart picker (x_enum_ref="CHART_IDS"). Fetched
  // alongside the dashboards so picking a saved chart is a dropdown rather than free-text. An
  // empty catalog produces an empty dropdown — operator must save a chart first (via the
  // TableView's chart-toggle Save modal) before referencing it here.
  const [charts, setCharts] = useState<Record<string, Record<string, unknown>>>({})
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
      // Charts fetch is best-effort: an empty / missing charts.toml just yields an empty picker.
      api.get<ChartsDoc>('/admin/config/charts/parsed').catch((): ChartsDoc => ({ path: '', charts: {} })),
    ])
      .then(([s, d, c]) => {
        // Lift the Dashboard $def to the top + thread the whole $defs so SchemaNavigator
        // resolves ChartWidget / KpiWidget / DashboardFilter when the user drills in.
        const defs = (s.dashboards.$defs ?? {}) as Record<string, JsonSchema>
        const dashboard = (defs.Dashboard ?? {}) as JsonSchema
        setDashboardSchema({ ...dashboard, $defs: defs })
        setEnums(s.framework_enums)
        setCharts(c.charts)
        setPath(d.path); setDoc(d.dashboards); setOriginal(JSON.stringify(d.dashboards))
        setSel((cur) => (cur && d.dashboards[cur] ? cur : Object.keys(d.dashboards)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // Materialise CHART_IDS on top of the bundled framework enums — fed into SchemaForm via
  // FrameworkEnumsContext so ChartWidget.chart renders as a SearchSelect of {id → label} from
  // the saved charts. Same pattern DictionaryBuilder uses for ENUM_IDS / LOOKUP_IDS.
  const augmentedEnums: FrameworkEnums = useMemo(() => {
    const base: FrameworkEnums = { ...(enums ?? {}) }
    const ids = Object.keys(charts ?? {}).sort()
    base.CHART_IDS = {
      label: 'Saved charts',
      values: ids.map((id) => {
        const c = charts[id] as { label?: string } | undefined
        return { value: id, label: c?.label || id, mono: id }
      }),
    }
    return base
  }, [enums, charts])

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

  const addDashboard = async () => {
    const id = (await modals.prompt({
      title: t('settings.dashboards.add'),
      message: t('settings.dashboards.namePrompt'),
    }))?.trim()
    if (!id) return
    if (doc && id in doc) { setSel(id); return }
    setDoc((p) => ({ ...(p ?? {}), [id]: { id, label: id, widgets: [] } }))
    setSel(id); setStatus(null)
  }
  const removeDashboard = async (id: string) => {
    const ok = await modals.confirm({
      title: t('settings.dashboards.delete'),
      message: t('settings.dashboards.confirmDelete', { name: id }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
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
    <FrameworkEnumsContext.Provider value={augmentedEnums}>
      <Shell>
        {/* One consolidated top toolbar — config path + status on the left, Add / Delete + Save /
            Reload on the right. Replaces the old "top action row + bottom Save Row" split — the
            operator never has to scroll past a long dashboards list to reach Save / Reload. */}
        <Toolbar>
          <ToolbarLeft>
            <Mono>{path}</Mono>
            {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
            {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
            {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
          </ToolbarLeft>
          <ToolbarRight>
            <Button $variant="ghost" $size="sm" onClick={addDashboard} disabled={busy}>
              <Plus size={13} /> {t('settings.dashboards.add')}
            </Button>
            {sel && selValue && (
              <Button $variant="danger" $size="sm" onClick={() => removeDashboard(sel)} disabled={busy} title={t('settings.dashboards.deleteOne', { name: sel })}>
                <Trash2 size={13} /> {t('settings.dashboards.deleteOne', { name: sel })}
              </Button>
            )}
            <ToolbarDivider />
            <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
            </Button>
            <Button $variant="ghost" $size="sm" onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : <RefreshCw size={13} />} {t('settings.pools.reloadFromDisk')}
            </Button>
          </ToolbarRight>
        </Toolbar>
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
          </NavCol>
          <FormCol>
            {sel && selValue ? (
              <Stack gap={12}>
                <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                    [dashboards.{sel}]
                  </strong>
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
      </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
