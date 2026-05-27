// Structured editor for ``config/charts.toml`` — the chart catalog. Same shape as
// :class:`DashboardsBuilder`: left column lists one chip per ``[charts.<id>]``, right column
// is a SchemaNavigator over the selected Chart. The navigator handles the nested ``spec``
// (ChartSpec) drill-in for free; the connector field renders as a SearchSelect via the
// CONNECTOR_NAMES enum we augment below.
//
// Charts are usually CREATED via the TableView's "Save chart" modal (which writes to the
// same ``charts.toml`` we edit here) — that flow picks the chart's columns from a live
// query result and is much nicer than typing column names by hand. This page exists for the
// REST of the lifecycle: inspect / edit / rename / delete an existing chart, fix its label,
// move it to a different connector, etc. The save flow validates against ``ChartsFile`` +
// rewrites the file via ``tomli_w`` (no surgical update — the file is generated content).
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, BarChart3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import {
  Banner,
  Button,
  Card,
  Centered,
  FrameworkEnumsContext,
  Row,
  SchemaNavigator,
  SpinnerRing,
  Stack,
  useModals,
  type FrameworkEnums,
  type JsonSchema,
} from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import type { ChartsDoc, ConfigSchemas } from '../../types/config'
import { colors, fontSize, fonts, radius } from '../../theme'

type Charts = Record<string, Record<string, unknown>>

// Layout: outer Shell flex-fills, top toolbar is fixed, the Split fills remaining height,
// only the inner panels scroll. Same pattern as DashboardsBuilder / PoolsBuilder.
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
const NavCol = styled.div`flex: 0 0 240px; display: flex; flex-direction: column; gap: 4px; min-height: 0;`
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
const Hint = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
  padding: 6px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  background: ${colors.bg.input}; line-height: 1.5;
`

export default function ChartsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { connectors } = useWorkspace()
  // Schema for the Chart $def — we navigate it with the full $defs map alongside so the
  // nested ``spec`` (ChartSpec) and any future sub-models resolve cleanly through
  // SchemaNavigator's drill-in.
  const [chartSchema, setChartSchema] = useState<JsonSchema | null>(null)
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
  const [doc, setDoc] = useState<Charts | null>(null)
  const [original, setOriginal] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ChartsDoc>('/admin/config/charts/parsed'),
    ])
      .then(([s, d]) => {
        // Lift the Chart $def to the top + thread the full $defs so SchemaNavigator can
        // resolve ChartSpec when the user drills into ``spec``.
        const defs = (s.charts.$defs ?? {}) as Record<string, JsonSchema>
        const chart = (defs.ChartConfig ?? {}) as JsonSchema
        setChartSchema({ ...chart, $defs: defs })
        setEnums(s.framework_enums)
        setDoc(d.charts); setOriginal(JSON.stringify(d.charts))
        setSel((cur) => (cur && d.charts[cur] ? cur : Object.keys(d.charts)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // Augment the bundled framework enums with CONNECTOR_NAMES so the schema's
  // ``connector`` field renders as a SearchSelect of the workspace's configured
  // connectors. Same pattern MenusBuilder uses to drive its CONNECTOR_NAMES picker.
  // We filter to SQL connectors since charts can only target a SQL read query.
  const augmentedEnums: FrameworkEnums = useMemo(() => {
    const base: FrameworkEnums = { ...(enums ?? {}) }
    const names = (connectors ?? []).filter((c) => c.type === 'sql').map((c) => c.name).sort()
    base.CONNECTOR_NAMES = {
      label: 'Connectors',
      values: names.map((n) => ({ value: n, label: n, mono: n })),
    }
    return base
  }, [enums, connectors])

  const dirty = useMemo(() => doc != null && JSON.stringify(doc) !== original, [doc, original])

  const update = (id: string, v: Record<string, unknown>) =>
    setDoc((p) => {
      // SchemaNavigator may have renamed ``id`` — the dict key follows so the on-disk
      // shape stays consistent (the parser injects ``id`` from the key, so a mismatch
      // fails validation).
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

  const addChart = async () => {
    const id = (await modals.prompt({
      title: t('settings.charts.add', 'Add chart'),
      message: t('settings.charts.namePrompt', 'New chart id (slug — letters, digits, underscores, hyphens):'),
    }))?.trim()
    if (!id) return
    if (doc && id in doc) { setSel(id); return }
    // Seed with a barely-valid skeleton — operator fills in connector + query + columns.
    // ChartSpec defaults (type=bar, aggregation=sum) carry, but x is required so the spec
    // panel is the first place the operator must visit.
    setDoc((p) => ({
      ...(p ?? {}),
      [id]: {
        id,
        label: id,
        connector: '',
        query: '',
        spec: { type: 'bar', x: '', y: [], aggregation: 'sum' },
      },
    }))
    setSel(id); setStatus(null)
  }

  const removeChart = async (id: string) => {
    const ok = await modals.confirm({
      title: t('settings.charts.delete', 'Delete chart'),
      message: t('settings.charts.confirmDelete', 'Delete chart "{{name}}"? Dashboards referencing it by id will show an error until you fix them.', { name: id }),
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
      await api.put<{ saved: boolean }>('/admin/config/charts/parsed', { charts: doc })
      const r = await api.post<{ charts?: string[] }>('/admin/reload')
      const list = (r.charts ?? []).join(', ') || `(${t('common.none')})`
      setStatus(t('settings.charts.saved', 'Saved & reloaded. Charts: {{charts}}', { charts: list }))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc || !chartSchema) return <Centered />
  const ids = Object.keys(doc).sort()
  const selValue = sel ? doc[sel] : null

  return (
    <FrameworkEnumsContext.Provider value={augmentedEnums}>
      <Shell>
        <Toolbar>
          <ToolbarLeft>
            {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
            {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
            {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
          </ToolbarLeft>
          <ToolbarRight>
            <Button $variant="ghost" $size="sm" onClick={addChart} disabled={busy}>
              <Plus size={13} /> {t('settings.charts.add', 'Add chart')}
            </Button>
            {sel && selValue && (
              <Button
                $variant="danger" $size="sm"
                onClick={() => removeChart(sel)} disabled={busy}
                title={t('settings.charts.deleteOne', 'Delete chart "{{name}}"', { name: sel })}
              >
                <Trash2 size={13} /> {t('settings.charts.deleteOne', 'Delete chart "{{name}}"', { name: sel })}
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
                const chart = doc[id] as { label?: string; connector?: string; query?: string }
                const subtitle = chart.connector && chart.query
                  ? `${chart.connector} · ${chart.query}`
                  : chart.label || undefined
                return (
                  <NavItem key={id} $active={id === sel} onClick={() => { setSel(id); setStatus(null) }}>
                    <BarChart3 size={13} />
                    <span className="text">
                      <span className="name">{id}</span>
                      {subtitle && <span className="lbl">{subtitle}</span>}
                    </span>
                  </NavItem>
                )
              })}
              {ids.length === 0 && (
                <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '2px 4px' }}>
                  {t('settings.charts.empty', 'No charts saved yet. Use "Save chart" in the TableView\'s Chart tab to create one, or click "Add chart" above to scaffold an empty one.')}
                </div>
              )}
            </NavList>
          </NavCol>
          <FormCol>
            {sel && selValue ? (
              <Stack gap={12}>
                <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                    [charts.{sel}]
                  </strong>
                </Row>
                {/* Quick orientation note — operators who landed here from Settings need a
                    nudge towards the easier creation path (TableView's Save chart modal
                    picks columns from a live query result, so they don't have to type column
                    names by hand). This page stays the right place to fix labels, rename
                    ids, change the connector / query, or delete unused charts. */}
                <Hint>
                  {t('settings.charts.hint',
                    'Tip: create new charts by clicking "Save chart" in the TableView\'s Chart tab — that flow picks columns from a live query. Use this page to rename, re-target, or delete existing charts.')}
                </Hint>
                <SchemaNavigator
                  root={{
                    label: sel,
                    schema: chartSchema,
                    value: selValue,
                    onChange: (v) => update(sel, v),
                  }}
                />
              </Stack>
            ) : (
              <Empty>
                {ids.length
                  ? t('settings.charts.pickOne', 'Pick a chart on the left.')
                  : t('settings.charts.empty', 'No charts saved yet. Use "Save chart" in the TableView\'s Chart tab to create one, or click "Add chart" above to scaffold an empty one.')}
              </Empty>
            )}
          </FormCol>
        </Split>
      </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
