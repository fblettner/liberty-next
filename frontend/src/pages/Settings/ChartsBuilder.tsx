// Charts settings — the catalog of `config/charts.toml`, organised by scope (the connector each
// chart reads from): `[charts.<scope>.<id>]`. A scope picker at the top, then the list of that
// scope's charts. Clicking one (or "Add chart") opens the visual ChartEditorModal (General +
// Chart-builder tabs, live preview). Saving rewrites charts.toml (PUT) + reloads; deleting too.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Plus, Trash2, BarChart3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, SpinnerRing, useModals } from '../../common'
import type { ChartsDoc } from '../../types/config'
import { ChartEditorModal, type ChartRecord } from './ChartEditorModal'
import { ScopeBar, type ScopeOption } from './ScopeBar'
import { AddScopeModal } from './AddScopeModal'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

// scope (connector) -> chart id -> chart body
type Charts = Record<string, Record<string, Record<string, unknown>>>

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; height: 100%;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const List = styled(Card)`flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px;`
const Item = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 9px 11px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer; min-width: 0;
  & > svg { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .sub { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`
const DelBtn = styled.button`
  flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px;
  border-radius: ${radius.sm}; border: 1px solid transparent; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { background: ${colors.red.bg}; color: ${colors.red.main}; border-color: ${colors.red.border}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`

export default function ChartsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { currentApp, connectors } = useWorkspace()
  const [scope, setScope] = useState<string>('')
  const [doc, setDoc] = useState<Charts | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [addScopeOpen, setAddScopeOpen] = useState(false)
  // editing === undefined → closed; null → new chart; ChartRecord → editing that chart.
  const [editing, setEditing] = useState<ChartRecord | null | undefined>(undefined)

  const load = () => {
    setError(null)
    api.get<ChartsDoc>('/admin/config/charts/parsed')
      .then((d) => setDoc(d.charts as Charts))
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // Persist the whole charts.toml + reload. Empty scopes (a connector picked via "Add scope" but
  // never filled) are pruned so they don't write a dangling `[charts.<scope>]` table.
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
    // A rename in the modal changes the id → drop the old key so the dict key matches `id`.
    if (editing && editing.id && editing.id !== id) delete byId[editing.id]
    byId[id] = record
    const next: Charts = { ...(doc ?? {}), [scope]: byId }
    setEditing(undefined)
    void persist(next, t('settings.charts.saved', 'Saved "{{id}}".', { id }))
  }

  const removeChart = async (id: string) => {
    const ok = await modals.confirm({
      title: t('settings.charts.delete', 'Delete chart'),
      message: t('settings.charts.confirmDelete', 'Delete chart "{{name}}"? Dashboards referencing it by id will show an error until you fix them.', { name: id }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const byId = { ...(doc?.[scope] ?? {}) }; delete byId[id]
    const next: Charts = { ...(doc ?? {}), [scope]: byId }
    void persist(next, t('settings.charts.deleted', 'Deleted "{{id}}".', { id }))
  }

  const scopes = useMemo<ScopeOption[]>(
    () => Object.keys(doc ?? {}).sort().map((v) => ({ value: v, label: v })),
    [doc],
  )
  // Default the scope once charts load — the workspace's selected app if it has charts, else first.
  useEffect(() => {
    if (!doc) return
    setScope((cur) => {
      if (cur && scopes.some((s) => s.value === cur)) return cur
      if (currentApp && scopes.some((s) => s.value === currentApp)) return currentApp
      return scopes[0]?.value ?? ''
    })
  }, [doc, scopes, currentApp])

  // Connectors that don't yet have a charts scope — offered by "Add scope".
  const addCandidates = useMemo(() => {
    const have = new Set(Object.keys(doc ?? {}))
    return (connectors ?? []).filter((c) => c.type === 'sql' && !have.has(c.name)).map((c) => c.name).sort()
  }, [doc, connectors])

  const ids = useMemo(() => Object.keys(doc?.[scope] ?? {}).sort(), [doc, scope])

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

      <List>
        {ids.map((id) => {
          const c = (doc[scope]?.[id] ?? {}) as { label?: string; query?: string }
          const sub = c.query ? `${scope} · ${c.query}` : c.label || ''
          return (
            <Item key={id} onClick={() => { setEditing({ ...(doc[scope][id] as unknown as ChartRecord), id, connector: scope }); setStatus(null) }}>
              <BarChart3 size={15} />
              <span className="text">
                <span className="name">{id}</span>
                {sub && <span className="sub">{sub}</span>}
              </span>
              <DelBtn onClick={(e) => { e.stopPropagation(); void removeChart(id) }}
                title={t('settings.charts.deleteOne', 'Delete chart "{{name}}"', { name: id })}>
                <Trash2 size={14} />
              </DelBtn>
            </Item>
          )
        })}
        {ids.length === 0 && (
          <Empty>{scope
            ? t('settings.charts.emptyScope', 'No charts in "{{scope}}" yet. Click "Add chart" to build one.', { scope })
            : t('settings.charts.empty', 'No charts yet. Use "Add scope" to pick a connector, then "Add chart".')}</Empty>
        )}
      </List>

      {editing !== undefined && (
        <ChartEditorModal
          initial={editing}
          scope={scope}
          takenIds={ids.filter((x) => x !== (editing?.id ?? ''))}
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
    </Shell>
  )
}
