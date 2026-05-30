// Dashboards settings — the catalog of `config/dashboards.toml`, organised by scope (the owning
// connector): `[dashboards.<scope>.<id>]`. A scope picker at the top, then that scope's dashboards
// in a filtered list with per-row Rename / Clone / Delete. Clicking a row (or "Add dashboard")
// opens the visual DashboardEditorModal — a fullscreen canvas of the live widget grid.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Plus, Trash2, LayoutDashboard, Search, Edit3, Copy, GitMerge } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, SpinnerRing, useModals } from '../../common'
import type { ChartsDoc, DashboardsDoc } from '../../types/config'
import type { SavedChartSpec } from '../../types/charts'
import { DashboardEditorModal } from './DashboardEditorModal'
import { ScopeBar, type ScopeOption } from './ScopeBar'
import { AddScopeModal } from './AddScopeModal'
import { FindDependenciesModal, type DependencySeed } from './FindDependenciesModal'
import { CloneWithDepsModal } from './CloneWithDepsModal'
import { DeleteWithDepsModal } from './DeleteWithDepsModal'
import { validateId as validateIdShared } from '../../services/idValidator'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

type Raw = Record<string, unknown>
type Dashboards = Record<string, Record<string, Raw>>
type ChartsByScope = Record<string, Record<string, Raw>>
type DashBody = { label?: string; description?: string | null; widgets?: unknown[]; filters?: unknown[] }

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; height: 100%;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const FilterBar = styled.div`
  display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 10px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.muted};
  flex-shrink: 0;
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; } }
`
const List = styled(Card)`flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px;`
const Item = styled.div`
  display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer; min-width: 0;
  & > .icon { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .label { font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .meta { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .actions { flex-shrink: 0; display: flex; gap: 4px; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`

const sstr = (v: unknown) => (typeof v === 'string' ? v : '')

export default function DashboardsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { currentApp, connectors } = useWorkspace()
  const [scope, setScope] = useState<string>('')
  const [doc, setDoc] = useState<Dashboards | null>(null)
  const [charts, setCharts] = useState<ChartsByScope>({})
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [addScopeOpen, setAddScopeOpen] = useState(false)
  const [depsSeeds, setDepsSeeds] = useState<DependencySeed[] | null>(null)
  const [editing, setEditing] = useState<Raw | undefined>(undefined)

  const load = () => {
    setError(null)
    Promise.all([
      api.get<DashboardsDoc>('/admin/config/dashboards/parsed'),
      api.get<ChartsDoc>('/admin/config/charts/parsed').catch((): ChartsDoc => ({ path: '', charts: {} })),
    ])
      .then(([d, c]) => { setDoc(d.dashboards as Dashboards); setCharts(c.charts as ChartsByScope) })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const persist = async (next: Dashboards, okMsg: string) => {
    setBusy(true); setError(null); setStatus(null)
    const pruned = Object.fromEntries(Object.entries(next).filter(([, byId]) => Object.keys(byId).length > 0))
    try {
      await api.put<{ saved: boolean }>('/admin/config/dashboards/parsed', { dashboards: pruned })
      await api.post('/admin/reload')
      setDoc(next)
      setStatus(okMsg)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const onEditorSave = (id: string, record: Raw) => {
    const prevId = sstr(editing?.id)
    const byId = { ...(doc?.[scope] ?? {}) }
    if (prevId && prevId !== id) delete byId[prevId]
    byId[id] = record
    void persist({ ...(doc ?? {}), [scope]: byId }, t('settings.dashboards.saved', 'Saved.'))
    setEditing(undefined)
  }

  const addDashboard = async () => {
    const existing = Object.keys(doc?.[scope] ?? {})
    const id = (await modals.prompt({
      title: t('settings.dashboards.add'),
      message: t('settings.dashboards.namePrompt'),
      placeholder: 'snake_case',
      validate: (v) => validateIdShared({ kind: 'dashboard', proposed: v, existing, mode: 'add' }),
    }))?.trim()
    if (!id) return
    setEditing({ id, label: id, widgets: [] })
    setStatus(null)
  }

  const validateDashboardId = (v: string, oldId: string | null) => {
    if (oldId && v === oldId) return { error: t('settings.tables.duplicateSameName', 'Pick a different name.') }
    const existing = Object.keys(doc?.[scope] ?? {}).filter((k) => k !== oldId)
    return validateIdShared({ kind: 'dashboard', proposed: v, existing, mode: oldId ? 'rename' : 'add', currentName: oldId ?? undefined })
  }

  const renameDashboard = async (oldId: string) => {
    const next = (await modals.prompt({
      title: t('settings.rename.button', 'Rename'),
      message: t('settings.dashboards.renamePrompt', 'New id for "{{name}}":', { name: oldId }),
      defaultValue: oldId, submitLabel: t('settings.rename.button', 'Rename'),
      validate: (v) => validateDashboardId(v.trim(), oldId),
    }))?.trim()
    if (!next || next === oldId) return
    const byId = { ...(doc?.[scope] ?? {}) }
    const src = byId[oldId]
    delete byId[oldId]
    byId[next] = { ...src, id: next }
    void persist({ ...(doc ?? {}), [scope]: byId }, t('settings.dashboards.renamed', 'Renamed.'))
  }

  // CloneWithDepsModal — operator can opt to also duplicate every referenced query
  // (KPI / chart / table / filter widgets) so the cloned dashboard is fully isolated.
  // /admin/clone-with-deps writes the dashboard + each cloned query atomically.
  const [cloneTarget, setCloneTarget] = useState<string | null>(null)
  const cloneDashboard = (oldId: string) => setCloneTarget(oldId)

  // Delete opens DeleteWithDepsModal — operator gets a checkbox to also delete the
  // dashboard's widget / filter queries when they're exclusively used by this dashboard.
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const removeDashboard = (id: string) => setDeleteTarget(id)

  const scopes = useMemo<ScopeOption[]>(
    () => Object.keys(doc ?? {}).sort().map((v) => ({ value: v, label: v })),
    [doc],
  )
  useEffect(() => {
    if (!doc) return
    setScope((cur) => {
      if (cur && scopes.some((s) => s.value === cur)) return cur
      if (currentApp && scopes.some((s) => s.value === currentApp)) return currentApp
      return scopes[0]?.value ?? ''
    })
  }, [doc, scopes, currentApp])

  const addCandidates = useMemo(() => {
    const have = new Set(Object.keys(doc ?? {}))
    return (connectors ?? []).filter((c) => !have.has(c.name)).map((c) => c.name).sort()
  }, [doc, connectors])

  const allIds = useMemo(() => Object.keys(doc?.[scope] ?? {}).sort(), [doc, scope])
  const needle = filter.trim().toLowerCase()
  const ids = needle
    ? allIds.filter((id) => {
        const d = (doc?.[scope]?.[id] ?? {}) as DashBody
        return id.toLowerCase().includes(needle)
          || (d.label ?? '').toLowerCase().includes(needle)
          || (d.description ?? '').toLowerCase().includes(needle)
      })
    : allIds
  const chartsCatalog = (charts[scope] ?? {}) as Record<string, { label?: string; connector?: string; query?: string; spec?: SavedChartSpec }>

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Centered />

  return (
    <Shell>
      <ScopeBar scopes={scopes} value={scope} onChange={setScope}
        emptyHint={t('settings.dashboards.noScopes', 'No dashboards yet — use "Add scope" to start.')}
        onAddScope={() => setAddScopeOpen(true)} />
      <Toolbar>
        <ToolbarLeft>
          {busy && <SpinnerRing size={14} thickness={2} />}
          {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
          {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        </ToolbarLeft>
        <ToolbarRight>
          <Button $variant="ghost" $size="sm" onClick={addDashboard} disabled={busy || !scope}>
            <Plus size={13} /> {t('settings.dashboards.add')}
          </Button>
        </ToolbarRight>
      </Toolbar>

      {allIds.length > 0 && (
        <FilterBar>
          <Search size={14} />
          <input value={filter} onChange={(e) => setFilter(e.target.value)}
            placeholder={t('settings.filterPlaceholder', 'Filter…')} />
        </FilterBar>
      )}

      <List>
        {ids.map((id) => {
          const dash = (doc[scope]?.[id] ?? {}) as DashBody
          const widgetsN = Array.isArray(dash.widgets) ? dash.widgets.length : 0
          const filtersN = Array.isArray(dash.filters) ? dash.filters.length : 0
          const meta = [
            t('settings.dash.widgetCount', '{{n}} widget(s)', { n: widgetsN }),
            filtersN > 0 && t('settings.dash.filterCount', '{{n}} filter(s)', { n: filtersN }),
            dash.description,
          ].filter(Boolean).join(' · ')
          return (
            <Item key={id} onClick={() => { setEditing({ ...(doc[scope][id]), id }); setStatus(null) }}>
              <LayoutDashboard className="icon" size={18} />
              <span className="text">
                <span className="name">{id}</span>
                {dash.label && <span className="label">{dash.label}</span>}
                {meta && <span className="meta">{meta}</span>}
              </span>
              <span className="actions" onClick={(e) => e.stopPropagation()}>
                <Button $variant="ghost" $size="sm" onClick={() => setDepsSeeds([{
                  kind: 'dashboard', name: id, label: `dashboard ${id}`,
                }])} disabled={busy}>
                  <GitMerge size={13} /> {t('findDeps.button', 'Find dependencies')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void renameDashboard(id)} disabled={busy}>
                  <Edit3 size={13} /> {t('settings.rename.button', 'Rename')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void cloneDashboard(id)} disabled={busy}>
                  <Copy size={13} /> {t('common.clone', 'Clone')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void removeDashboard(id)} disabled={busy} style={{ color: colors.red.main }}>
                  <Trash2 size={13} /> {t('common.delete', 'Delete')}
                </Button>
              </span>
            </Item>
          )
        })}
        {ids.length === 0 && (
          <Empty>
            {!scope
              ? t('settings.dashboards.empty')
              : allIds.length === 0
                ? t('settings.dashboards.emptyScope', 'No dashboards in "{{scope}}" yet.', { scope })
                : t('common.noMatches', 'No matches')}
          </Empty>
        )}
      </List>

      {editing !== undefined && (
        <DashboardEditorModal
          initial={editing}
          scope={scope}
          chartsCatalog={chartsCatalog}
          onSave={onEditorSave}
          onClose={() => setEditing(undefined)}
        />
      )}
      {addScopeOpen && (
        <AddScopeModal
          candidates={addCandidates}
          title={t('settings.dashboards.addScope', 'Add an owning connector')}
          emptyHint={t('settings.dashboards.addScopeEmpty', 'Every connector already has a dashboards scope.')}
          onPick={(c) => { setDoc((p) => ({ ...(p ?? {}), [c]: p?.[c] ?? {} })); setScope(c); setStatus(null) }}
          onClose={() => setAddScopeOpen(false)}
        />
      )}
      {depsSeeds && <FindDependenciesModal seeds={depsSeeds} onClose={() => setDepsSeeds(null)} />}
      {cloneTarget && (
        <CloneWithDepsModal
          kind="dashboard"
          name={cloneTarget}
          scope={scope || null}
          existingNames={Object.keys(doc?.[scope] ?? {})}
          onCloned={() => load()}
          onClose={() => setCloneTarget(null)}
        />
      )}
      {deleteTarget && (
        <DeleteWithDepsModal
          kind="dashboard"
          name={deleteTarget}
          scope={scope || null}
          onDeleted={() => load()}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </Shell>
  )
}
