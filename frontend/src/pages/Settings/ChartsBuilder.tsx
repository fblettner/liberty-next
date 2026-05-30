// Charts settings — the catalog of `config/charts.toml`, organised by scope (the connector each
// chart reads from): `[charts.<scope>.<id>]`. A scope picker at the top, then a filtered list of
// that scope's charts with per-row Rename / Clone / Delete actions. Clicking a row (or "Add chart")
// opens the visual ChartEditorModal (General + Chart-builder tabs, live preview). Saving rewrites
// charts.toml (PUT) + reloads.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Plus, Trash2, BarChart3, Search, Edit3, Copy, GitBranch, GitMerge } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, SpinnerRing, Tag, useModals } from '../../common'
import type { ChartsDoc } from '../../types/config'
import { ChartEditorModal, type ChartRecord } from './ChartEditorModal'
import { ScopeBar, type ScopeOption } from './ScopeBar'
import { AddScopeModal } from './AddScopeModal'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import { FindDependenciesModal, type DependencySeed } from './FindDependenciesModal'
import { CloneWithDepsModal } from './CloneWithDepsModal'
import { DeleteWithDepsModal } from './DeleteWithDepsModal'
import { validateId as validateIdShared } from '../../services/idValidator'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

// scope (connector) -> chart id -> chart body
type Charts = Record<string, Record<string, Record<string, unknown>>>
type ChartBody = { label?: string; description?: string; query?: string; spec?: { type?: string; x?: string; y?: string[] } }

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

export default function ChartsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { currentApp, connectors } = useWorkspace()
  const [scope, setScope] = useState<string>('')
  const [doc, setDoc] = useState<Charts | null>(null)
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [addScopeOpen, setAddScopeOpen] = useState(false)
  const [usagesTarget, setUsagesTarget] = useState<FindUsagesTarget | null>(null)
  const [depsSeeds, setDepsSeeds] = useState<DependencySeed[] | null>(null)
  // editing === undefined → closed; null → new chart; ChartRecord → editing that chart.
  const [editing, setEditing] = useState<ChartRecord | null | undefined>(undefined)

  const load = () => {
    setError(null)
    api.get<ChartsDoc>('/admin/config/charts/parsed')
      .then((d) => setDoc(d.charts as Charts))
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // Persist + reload. Empty scopes are pruned so they don't write a dangling `[charts.<scope>]`.
  const persist = async (next: Charts, okMsg: string) => {
    setBusy(true); setError(null); setStatus(null)
    const pruned = Object.fromEntries(Object.entries(next).filter(([, byId]) => Object.keys(byId).length > 0))
    try {
      await api.put<{ saved: boolean }>('/admin/config/charts/parsed', { charts: pruned })
      await api.post('/admin/reload')
      setDoc(next)
      setStatus(okMsg)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const onModalSave = (id: string, record: Record<string, unknown>) => {
    const byId = { ...(doc?.[scope] ?? {}) }
    if (editing && editing.id && editing.id !== id) delete byId[editing.id]
    byId[id] = record
    const next: Charts = { ...(doc ?? {}), [scope]: byId }
    setEditing(undefined)
    void persist(next, t('settings.charts.saved', 'Saved.'))
  }

  const validateChartId = (v: string, oldId: string | null) => {
    if (oldId && v === oldId) return { error: t('settings.tables.duplicateSameName', 'Pick a different name.') }
    const existing = Object.keys(doc?.[scope] ?? {}).filter((k) => k !== oldId)
    return validateIdShared({ kind: 'chart', proposed: v, existing, mode: oldId ? 'rename' : 'add', currentName: oldId ?? undefined })
  }

  const renameChart = async (oldId: string) => {
    const next = (await modals.prompt({
      title: t('settings.rename.button', 'Rename'),
      message: t('settings.charts.renamePrompt', 'New id for "{{name}}":', { name: oldId }),
      defaultValue: oldId, submitLabel: t('settings.rename.button', 'Rename'),
      validate: (v) => validateChartId(v.trim(), oldId),
    }))?.trim()
    if (!next || next === oldId) return
    const byId = { ...(doc?.[scope] ?? {}) }
    const src = byId[oldId] as Record<string, unknown>
    delete byId[oldId]
    byId[next] = { ...src, id: next }
    void persist({ ...(doc ?? {}), [scope]: byId }, t('settings.charts.renamed', 'Renamed.'))
  }

  // CloneWithDepsModal — operator can opt to also duplicate the chart's referenced
  // query so the clone is fully isolated (editing the cloned chart's SQL doesn't
  // bleed into the source). Backend /admin/clone-with-deps writes both atomically.
  const [cloneTarget, setCloneTarget] = useState<string | null>(null)
  const cloneChart = (oldId: string) => setCloneTarget(oldId)

  // Delete opens DeleteWithDepsModal — operator gets a checkbox to also delete the
  // chart's referenced query when it's exclusively used by this chart. Backend's
  // safe-delete walker preserves any query also used by another entity.
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const removeChart = (id: string) => setDeleteTarget(id)

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
    return (connectors ?? []).filter((c) => c.type === 'sql' && !have.has(c.name)).map((c) => c.name).sort()
  }, [doc, connectors])

  const allIds = useMemo(() => Object.keys(doc?.[scope] ?? {}).sort(), [doc, scope])
  const needle = filter.trim().toLowerCase()
  const ids = needle
    ? allIds.filter((id) => {
        const c = (doc?.[scope]?.[id] ?? {}) as ChartBody
        return id.toLowerCase().includes(needle)
          || (c.label ?? '').toLowerCase().includes(needle)
          || (c.query ?? '').toLowerCase().includes(needle)
      })
    : allIds

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Centered />

  return (
    <Shell>
      <ScopeBar scopes={scopes} value={scope} onChange={setScope}
        emptyHint={t('settings.charts.noScopes', 'No charts yet — use "Add scope" to start.')}
        onAddScope={() => setAddScopeOpen(true)} />
      <Toolbar>
        <ToolbarLeft>
          {busy && <SpinnerRing size={14} thickness={2} />}
          {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
          {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        </ToolbarLeft>
        <ToolbarRight>
          <Button $variant="ghost" $size="sm" onClick={() => { setEditing(null); setStatus(null) }} disabled={busy || !scope}>
            <Plus size={13} /> {t('settings.charts.add', 'Add chart')}
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
          const c = (doc[scope]?.[id] ?? {}) as ChartBody
          const type = c.spec?.type
          const meta = [c.query && `${scope}.${c.query}`, type, c.spec?.x && `x: ${c.spec.x}`].filter(Boolean).join(' · ')
          // Customer-override badge — see ScreensBuilder for the rationale.
          const isOverride = (c as unknown as { override?: boolean }).override === true
          return (
            <Item key={id} onClick={() => { setEditing({ ...(doc[scope][id] as unknown as ChartRecord), id, connector: scope }); setStatus(null) }}>
              <BarChart3 className="icon" size={18} />
              <span className="text">
                <span className="name">
                  {id}
                  {isOverride && (
                    <Tag $tone="orange" style={{ marginLeft: 6, fontSize: fontSize.micro }}>
                      {t('settings.override.badge', 'override')}
                    </Tag>
                  )}
                </span>
                {c.label && <span className="label">{c.label}</span>}
                {meta && <span className="meta">{meta}</span>}
              </span>
              <span className="actions" onClick={(e) => e.stopPropagation()}>
                <Button $variant="ghost" $size="sm" onClick={() => setUsagesTarget({
                  kind: 'chart', name: id, scope, label: `chart ${scope}.${id}`,
                })} disabled={busy}>
                  <GitBranch size={13} /> {t('findUsages.button', 'Find usages')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => setDepsSeeds([{
                  kind: 'chart', name: id, scope, label: `chart ${scope}.${id}`,
                }])} disabled={busy}>
                  <GitMerge size={13} /> {t('findDeps.button', 'Find dependencies')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void renameChart(id)} disabled={busy}>
                  <Edit3 size={13} /> {t('settings.rename.button', 'Rename')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void cloneChart(id)} disabled={busy}>
                  <Copy size={13} /> {t('common.clone', 'Clone')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void removeChart(id)} disabled={busy} style={{ color: colors.red.main }}>
                  <Trash2 size={13} /> {t('common.delete', 'Delete')}
                </Button>
              </span>
            </Item>
          )
        })}
        {ids.length === 0 && (
          <Empty>
            {!scope
              ? t('settings.charts.empty', 'No charts yet. Use "Add scope" to pick a connector, then "Add chart".')
              : allIds.length === 0
                ? t('settings.charts.emptyScope', 'No charts in "{{scope}}" yet.', { scope })
                : t('common.noMatches', 'No matches')}
          </Empty>
        )}
      </List>

      {editing !== undefined && (
        <ChartEditorModal
          initial={editing}
          scope={scope}
          takenIds={allIds.filter((x) => x !== (editing?.id ?? ''))}
          onSave={onModalSave}
          onClose={() => setEditing(undefined)}
        />
      )}
      {addScopeOpen && (
        <AddScopeModal
          candidates={addCandidates}
          title={t('settings.charts.addScope', 'Add a connector scope')}
          emptyHint={t('settings.charts.addScopeEmpty', 'Every SQL connector already has a charts scope.')}
          onPick={(c) => { setDoc((p) => ({ ...(p ?? {}), [c]: p?.[c] ?? {} })); setScope(c); setStatus(null) }}
          onClose={() => setAddScopeOpen(false)}
        />
      )}
      {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
      {depsSeeds && <FindDependenciesModal seeds={depsSeeds} onClose={() => setDepsSeeds(null)} />}
      {cloneTarget && (
        <CloneWithDepsModal
          kind="chart"
          name={cloneTarget}
          scope={scope}
          existingNames={Object.keys(doc?.[scope] ?? {})}
          onCloned={() => load()}
          onClose={() => setCloneTarget(null)}
        />
      )}
      {deleteTarget && (
        <DeleteWithDepsModal
          kind="chart"
          name={deleteTarget}
          scope={scope}
          onDeleted={() => load()}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </Shell>
  )
}
