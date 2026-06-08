// Change packages — every tracked data modification captured into the active package for its
// connector. Master list on the left, per-package detail on the right: entries grouped by entity
// with old→new diffs. The detail header carries the lifecycle actions (submit / approve / reject)
// and each draft entry can be excluded / re-included (cherry-pick).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Layers, ArrowRight, Send, Check, X, EyeOff, Eye, Download, Upload, ChevronRight, ChevronDown, RefreshCw, Trash2, Search, Inbox } from 'lucide-react'
import { Banner, Button, useModals } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ApplyBundlePanel } from './ApplyBundleModal'
import { PostApplyEditor } from './PostApplyEditor'

type Pkg = {
  id: string; application: string; name: string; status: string; description: string | null
  created_by: string | null; created_at: string | null
  submitted_by: string | null; submitted_at: string | null
  approved_by: string | null; approved_at: string | null
  post_apply_ids: string[]
  entry_count: number
}
type Entry = {
  id: string; seq: number; connector: string; query: string; operation: string
  entity: string | null; entity_key: Record<string, unknown> | null
  new_values: Record<string, unknown> | null; old_values: Record<string, unknown> | null
  status: string; captured_by: string | null; captured_at: string | null
}
type PkgDetail = Pkg & { entries: Entry[] }
type AppliedOp = { connector?: string; query?: string; operation?: string; entity_key?: Record<string, unknown> | null; post_apply?: boolean; label?: string | null }
type AppliedResult = { index?: number; op?: AppliedOp; status: string; detail?: string; rowcount?: number }
type Applied = {
  id: string; source_package_id: string | null; name: string; application: string | null
  checksum: string | null; op_count: number; status: string
  summary: Record<string, number> | null; details: AppliedResult[] | null
  applied_by: string | null; applied_at: string | null
}

const Tabs = styled.div`display: flex; align-items: center; gap: 4px; margin-bottom: 14px;`
const TabBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 6px;
  height: 30px; padding: 0 12px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const APPLIED_OP_TONE: Record<string, string> = {
  applied: colors.green.main, would_apply: colors.green.main, would_force: colors.orange.main,
  unverified: colors.orange.main, conflict: colors.red.main, error: colors.red.main,
}
const Wrap = styled.div`display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start;`
const List = styled.div`display: flex; flex-direction: column; gap: 6px;`
const FilterBox = styled.div`
  display: flex; align-items: center; gap: 6px; padding: 5px 9px; margin-bottom: 4px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted};
  & input { flex: 1; background: none; border: none; outline: none; color: ${colors.text.primary};
    font-family: ${fonts.sans}; font-size: ${fontSize.sm}; }
  & input::placeholder { color: ${colors.text.muted}; }
  & .clear { background: none; border: none; cursor: pointer; color: ${colors.text.muted}; display: inline-flex; padding: 0; }
  & .clear:hover { color: ${colors.text.secondary}; }
`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; margin-bottom: 12px; line-height: 1.5;`
const Item = styled.button<{ $active: boolean }>`
  text-align: left; display: flex; flex-direction: column; gap: 2px; padding: 9px 11px; cursor: pointer;
  border: 1px solid ${(p) => (p.$active ? colors.blue.border : colors.border)}; border-radius: ${radius.md};
  background: ${(p) => (p.$active ? 'var(--hover-subtle)' : colors.bg.input)};
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .meta { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  &:hover { border-color: ${colors.blue.border}; }
`
const Badge = styled.span<{ $tone: string }>`
  display: inline-block; font-size: ${fontSize.micro}; font-weight: 600; padding: 1px 7px; border-radius: 999px;
  text-transform: uppercase; letter-spacing: .03em;
  color: ${(p) => p.$tone}; border: 1px solid ${(p) => p.$tone}; background: transparent;
`
const AppliedPanel = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; margin-top: 14px; background: ${colors.bg.input};
  & > .head { display: flex; align-items: center; gap: 8px; padding: 9px 11px; color: ${colors.text.secondary};
    font-size: ${fontSize.sm}; font-weight: 600; font-family: ${fonts.sans}; border-bottom: 1px solid ${colors.border}; }
  & > .head .count { color: ${colors.text.muted}; font-weight: 400; }
  & .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 7px 11px; cursor: pointer;
    border-top: 1px solid ${colors.border}; }
  & .row:first-of-type { border-top: none; }
  & .row:hover { background: var(--hover-subtle); }
  & .row .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .row .meta { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .row .when { margin-left: auto; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .det { display: flex; flex-direction: column; gap: 3px; padding: 4px 11px 10px 30px; }
  & .det .op { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: ${fontSize.micro}; }
  & .det .op .key { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & .det .op .q { font-family: ${fonts.mono}; color: ${colors.text.muted}; }
  & .det .op .dt { color: ${colors.text.secondary}; }
`
const Group = styled.div`margin-bottom: 14px;`
const Row = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 9px 11px; margin-bottom: 6px; background: ${colors.bg.input};
  & .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  & .head.clickable { cursor: pointer; user-select: none; }
  & .chev { color: ${colors.text.muted}; flex-shrink: 0; }
  & .key { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .q { font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; }
  & .count { font-size: ${fontSize.micro}; color: ${colors.text.secondary}; }
`
const MoreFields = styled.button`
  grid-column: 1 / -1; justify-self: start; margin-top: 2px; background: none; border: none; cursor: pointer;
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-family: ${fonts.sans}; padding: 0;
  &:hover { color: ${colors.text.secondary}; text-decoration: underline; }
`
const GroupToggleHead = styled.button`
  display: flex; align-items: center; gap: 6px; width: 100%; text-align: left; background: none; border: none;
  cursor: pointer; padding: 0; margin: 6px 0; color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-weight: 600;
  & .count { color: ${colors.text.muted}; font-weight: 400; }
  & .keys { color: ${colors.text.muted}; font-weight: 400; font-family: ${fonts.mono}; font-size: ${fontSize.micro};
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { color: ${colors.text.primary}; }
`
const Diff = styled.div`
  display: grid; grid-template-columns: minmax(80px, max-content) 1fr; gap: 2px 10px; font-size: ${fontSize.micro}; font-family: ${fonts.mono};
  & .f { color: ${colors.text.muted}; }
  & .old { color: ${colors.red.main}; text-decoration: line-through; }
  & .new { color: ${colors.green.main}; }
  & .same { color: ${colors.text.secondary}; }
`

const DetailHead = styled.div`
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px;
  & .title { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .who { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .actions { display: flex; gap: 6px; margin-left: auto; }
`
const ExBtn = styled.button`
  display: inline-flex; align-items: center; gap: 4px; margin-left: auto; flex-shrink: 0;
  background: none; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; padding: 1px 7px; cursor: pointer;
  &:hover { border-color: ${colors.blue.border}; color: ${colors.text.secondary}; }
`

const STATUS_TONE: Record<string, string> = {
  draft: colors.blue.main, pending: colors.orange.main, approved: colors.green.main,
  exported: colors.text.muted, promoted: colors.text.muted, rejected: colors.red.main,
}
const OP_TONE: Record<string, string> = {
  INSERT: colors.green.main, UPDATE: colors.blue.main, DELETE: colors.red.main,
  CALL_API: colors.orange.main, CALL_PLUGIN: colors.orange.main,
}
const INVOCATION_OPS = new Set(['CALL_API', 'CALL_PLUGIN'])

function fmt(v: unknown): string { return v === null || v === undefined || v === '' ? '∅' : String(v) }

// Short local date+time for the package list (no seconds). Returns '' for a missing timestamp.
function fmtDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// The lifecycle date worth showing on a package: approved (also covers exported, since exported
// keeps approved_at) → submitted → created. Returns a {label, when} or null. Exported time is
// intentionally not surfaced.
function pkgDate(p: Pkg): { label: string; when: string } | null {
  if (p.approved_at) return { label: p.status === 'rejected' ? 'rejected' : 'approved', when: fmtDate(p.approved_at) }
  if (p.submitted_at) return { label: 'submitted', when: fmtDate(p.submitted_at) }
  if (p.created_at) return { label: 'created', when: fmtDate(p.created_at) }
  return null
}

// Split an entry's fields into changed (incl. insert-added / delete-removed) vs unchanged, so the
// collapsed view can show a count and the expanded view can hide the noise of untouched columns.
function classifyFields(oldV: Record<string, unknown>, newV: Record<string, unknown>) {
  const fields = [...new Set([...Object.keys(oldV), ...Object.keys(newV)])].sort()
  const changed: string[] = []; const unchanged: string[] = []
  for (const f of fields) {
    const hasOld = f in oldV; const hasNew = f in newV
    if (hasOld && hasNew && fmt(oldV[f]) === fmt(newV[f])) unchanged.push(f)
    else changed.push(f)
  }
  return { fields, changed, unchanged }
}

export default function ChangePackagesBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const [packages, setPackages] = useState<Pkg[] | null>(null)
  const [selId, setSelId] = useState<string | null>(null)
  const [detail, setDetail] = useState<PkgDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Two tabs, like the Package page: review/manage change packages, or apply an imported bundle.
  const [mode, setMode] = useState<'changes' | 'apply'>('changes')
  const [filter, setFilter] = useState('')
  // Target-side import log — bundles applied to THIS environment (shown in the Apply tab). Each row
  // expands to its per-op detail.
  const [applied, setApplied] = useState<Applied[] | null>(null)
  const [openApplied, setOpenApplied] = useState<Set<string>>(new Set())
  // Collapsible UI state — a package can hold hundreds of entries, so default to collapsed diffs
  // (just the summary row) and let the operator drill into the ones they care about. Group + entry
  // expansion are remembered per package id while it stays selected.
  const [openEntries, setOpenEntries] = useState<Set<string>>(new Set())
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())
  const [showUnchanged, setShowUnchanged] = useState<Set<string>>(new Set())
  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, id: string) => {
    const n = new Set(set); n.has(id) ? n.delete(id) : n.add(id); setter(n)
  }

  const loadList = useCallback(() =>
    api.get<{ packages: Pkg[] }>('/admin/changesets')
      .then((d) => { setPackages(d.packages); if (d.packages[0]) setSelId((s) => s ?? d.packages[0].id) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e))), [])
  const loadDetail = useCallback((id: string) =>
    api.get<PkgDetail>(`/admin/changesets/${encodeURIComponent(id)}`)
      .then(setDetail)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e))), [])
  const loadApplied = useCallback(() =>
    api.get<{ applied: Applied[] }>('/admin/changesets/applied')
      .then((d) => setApplied(d.applied))
      // The import log is secondary — a failure (endpoint missing on an un-restarted backend,
      // changesets disabled) shouldn't error the whole page. Surface in the console for diagnosis
      // and leave the list empty.
      .catch((e) => { console.warn('applied-bundles load failed', e); setApplied([]) }), [])  // eslint-disable-line no-console

  useEffect(() => { void loadList() }, [loadList])
  // Reload the import log on mount AND whenever the Apply tab is (re)opened — so it's fresh when you
  // come back to it, not stale from the initial mount.
  useEffect(() => { if (mode === 'apply') void loadApplied() }, [mode, loadApplied])
  useEffect(() => { if (!selId) { setDetail(null); return } void loadDetail(selId) }, [selId, loadDetail])
  // Initialise collapse state when SWITCHING package (keyed on id, so a refresh after
  // exclude/include doesn't reset what the operator expanded). Groups open; entry diffs collapsed
  // by default so the package opens as a scannable summary list (one row per change).
  useEffect(() => {
    if (!detail) return
    setOpenGroups(new Set(detail.entries.map((e) => e.entity || e.query)))
    setOpenEntries(new Set())
    setShowUnchanged(new Set())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.id])

  // Lifecycle action (submit/approve/reject) or per-entry exclude/include, then refresh both panes.
  const act = useCallback(async (path: string) => {
    if (!selId) return
    setBusy(true); setError(null)
    try {
      await api.post(`/admin/changesets/${encodeURIComponent(selId)}${path}`)
      await Promise.all([loadList(), loadDetail(selId)])
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }, [selId, loadList, loadDetail])

  // Export the approved package → download the promotion bundle as a JSON file, then refresh
  // (the package flips to "exported"). You then apply that file on the target (prod) Liberty.
  const exportPkg = useCallback(async () => {
    if (!selId) return
    setBusy(true); setError(null)
    try {
      const bundle = await api.get<{ package?: { name?: string } }>(`/admin/changesets/${encodeURIComponent(selId)}/export`)
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${(bundle.package?.name || 'package').replace(/[^\w.-]+/g, '_')}.changeset.json`
      a.click()
      URL.revokeObjectURL(url)
      await Promise.all([loadList(), loadDetail(selId)])
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }, [selId, loadList, loadDetail])

  // Manual refresh — the panel doesn't live-update, so a write captured in another tab won't show
  // until this is hit (or the tab is re-opened). Reloads the list + the open package.
  const refresh = useCallback(async () => {
    setBusy(true); setError(null)
    try { await Promise.all([loadList(), loadApplied(), selId ? loadDetail(selId) : Promise.resolve()]) }
    catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
    finally { setBusy(false) }
  }, [selId, loadList, loadApplied, loadDetail])

  // Delete a package (any status) — only the package log goes; the captured rows already live in
  // their tables. Confirm via the themed dialog (never window.confirm). Re-point the selection.
  const deletePkg = useCallback(async (p: Pkg) => {
    const ok = await modals.confirm({
      title: t('settings.changes.deleteTitle', 'Delete package?'),
      message: t('settings.changes.deleteMsg',
        'Delete "{{name}}" and its {{n}} captured change(s)? The rows already written to the database are not affected — only this package log is removed.',
        { name: p.name, n: p.entry_count }),
      variant: 'danger',
      confirmLabel: t('common.delete', 'Delete'),
    })
    if (!ok) return
    setBusy(true); setError(null)
    try {
      await api.del(`/admin/changesets/${encodeURIComponent(p.id)}`)
      if (selId === p.id) { setSelId(null); setDetail(null) }
      await loadList()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }, [modals, t, selId, loadList])

  // Left-panel filter — names are just "Package N" (not useful), so match the typed text against
  // status, application AND the displayed lifecycle dates (the label + the formatted timestamps),
  // so e.g. "approved", "2026-06" or a day all narrow the list.
  const visiblePackages = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return packages ?? []
    return (packages ?? []).filter((p) => {
      const d = pkgDate(p)
      const hay = [
        p.name, p.status, p.application, d?.label, d?.when,
        fmtDate(p.created_at), fmtDate(p.submitted_at), fmtDate(p.approved_at),
      ].filter(Boolean).join(' ').toLowerCase()
      return hay.includes(q)
    })
  }, [packages, filter])

  // Group the detail's entries by entity (Users / Roles / …); fall back to the query name.
  const grouped = useMemo(() => {
    const m = new Map<string, Entry[]>()
    for (const e of detail?.entries ?? []) {
      const k = e.entity || e.query
      ;(m.get(k) ?? m.set(k, []).get(k)!).push(e)
    }
    return [...m.entries()]
  }, [detail])

  if (error && !packages) return <Banner $tone="error">{error}</Banner>
  if (!packages) return <Sub>{t('common.loading', 'Loading…')}</Sub>

  return (
    <>
      <Tabs>
        <TabBtn $active={mode === 'changes'} onClick={() => setMode('changes')}>
          <Layers size={13} /> {t('settings.changes.changesTab', 'Changes')}
        </TabBtn>
        <TabBtn $active={mode === 'apply'} onClick={() => setMode('apply')}>
          <Upload size={13} /> {t('settings.changes.applyTab', 'Apply')}
        </TabBtn>
        <div style={{ flex: 1 }} />
        <Button $size="sm" $variant="ghost" disabled={busy} onClick={() => void refresh()}>
          <RefreshCw size={13} /> {t('common.refresh', 'Refresh')}
        </Button>
      </Tabs>
      {error && <Banner $tone="error">{error}</Banner>}

      {mode === 'apply' ? (
        <>
          <ApplyBundlePanel onApplied={loadApplied} />
          {applied && applied.length > 0 && (
            <AppliedPanel>
              <div className="head">
                <Inbox size={14} />
                <span>{t('settings.changes.appliedTitle', 'Applied bundles')}</span>
                <span className="count">{applied.length}</span>
              </div>
              {applied.map((a) => {
                const summary = a.summary ? Object.entries(a.summary).map(([k, v]) => `${v} ${k}`).join(' · ') : ''
                const open = openApplied.has(a.id)
                return (
                  <div key={a.id}>
                    <div className="row" onClick={() => toggle(openApplied, setOpenApplied, a.id)}>
                      {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      <Badge $tone={a.status === 'partial' ? colors.orange.main : colors.green.main}>{a.status}</Badge>
                      <span className="name">{a.name}</span>
                      <span className="meta">{a.application} · {t('settings.changes.opsCount', '{{n}} op(s)', { n: a.op_count })}{summary ? ` · ${summary}` : ''}</span>
                      <span className="when">{fmtDate(a.applied_at)}{a.applied_by ? ` · ${a.applied_by}` : ''}</span>
                    </div>
                    {open && a.details && a.details.length > 0 && (
                      <div className="det">
                        {a.details.map((r, i) => (
                          <div className="op" key={i}>
                            <Badge $tone={APPLIED_OP_TONE[r.status] ?? colors.text.muted}>{r.status.replace('_', ' ')}</Badge>
                            {r.op?.post_apply && <Badge $tone={colors.blue.main}>{t('settings.changes.postApply', 'post-apply')}</Badge>}
                            <span className="key">{r.op?.post_apply ? (r.op?.label || r.op?.operation) : `${r.op?.operation} ${r.op?.entity_key ? Object.entries(r.op.entity_key).map(([k, v]) => `${k}=${fmt(v)}`).join(' · ') : ''}`}</span>
                            <span className="q">{r.op?.connector}.{r.op?.query}</span>
                            {r.detail && <span className="dt">— {r.detail}</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </AppliedPanel>
          )}
        </>
      ) : (
        <>
        {packages.length === 0 ? (
        <Sub>{t('settings.changes.empty', 'No change packages yet — edit a record on a change-tracked screen to open one.')}</Sub>
      ) : (
        <Wrap>
          <List>
            <FilterBox>
              <Search size={13} />
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={t('settings.changes.filter', 'Filter by name / status…')}
              />
              {filter && <button type="button" className="clear" onClick={() => setFilter('')}><X size={12} /></button>}
            </FilterBox>
            {visiblePackages.length === 0 ? (
              <Sub style={{ margin: '4px 2px' }}>{t('settings.changes.noMatch', 'No packages match.')}</Sub>
            ) : visiblePackages.map((p) => {
              const d = pkgDate(p)
              return (
              <Item key={p.id} $active={p.id === selId} onClick={() => setSelId(p.id)}>
                <span className="name">{p.name}</span>
                <span className="meta">
                  <Badge $tone={STATUS_TONE[p.status] ?? colors.text.muted}>{p.status}</Badge>
                  {' '}{p.application} · {t('settings.changes.nChanges', '{{n}} change(s)', { n: p.entry_count })}
                </span>
                {d && <span className="meta">{d.label} · {d.when}</span>}
              </Item>
              )
            })}
          </List>
          <div>
            {detail && (
              <DetailHead>
                <span className="title">{detail.name}</span>
                <Badge $tone={STATUS_TONE[detail.status] ?? colors.text.muted}>{detail.status}</Badge>
                {detail.approved_by && <span className="who">{detail.status} · {detail.approved_by}</span>}
                <span className="actions">
                  {detail.entries.length > 0 && (
                    <Button $size="sm" $variant="ghost" onClick={() =>
                      setOpenEntries((s) => s.size >= detail.entries.length ? new Set() : new Set(detail.entries.map((e) => e.id)))}>
                      {openEntries.size >= detail.entries.length
                        ? t('settings.changes.collapseAll', 'Collapse all')
                        : t('settings.changes.expandAll', 'Expand all')}
                    </Button>
                  )}
                  {detail.status === 'draft' && (
                    <Button $size="sm" $variant="primary" disabled={busy} onClick={() => void act('/submit')}>
                      <Send size={13} /> {t('settings.changes.submit', 'Submit')}
                    </Button>
                  )}
                  {detail.status === 'pending' && (
                    <>
                      <Button $size="sm" $variant="primary" disabled={busy} onClick={() => void act('/approve')}>
                        <Check size={13} /> {t('settings.changes.approve', 'Approve')}
                      </Button>
                      <Button $size="sm" $variant="ghost" disabled={busy} style={{ color: colors.red.main }} onClick={() => void act('/reject')}>
                        <X size={13} /> {t('settings.changes.reject', 'Reject')}
                      </Button>
                    </>
                  )}
                  {(detail.status === 'approved' || detail.status === 'exported') && (
                    <>
                      {(detail.post_apply_ids?.length ?? 0) > 0 && (
                        <span style={{ fontSize: fontSize.micro, color: colors.text.muted, alignSelf: 'center' }}
                          title={t('settings.changes.bundleIncludesTip', 'These run-once post-apply steps — from the screens that contributed to this package — are appended to the exported bundle: {{ids}}', { ids: detail.post_apply_ids.join(', ') })}>
                          {t('settings.changes.bundleIncludes', '+ {{n}} post-apply', { n: detail.post_apply_ids.length })}
                        </span>
                      )}
                      <Button $size="sm" $variant="primary" disabled={busy} onClick={() => void exportPkg()}>
                        <Download size={13} /> {t('settings.changes.export', 'Export bundle')}
                      </Button>
                    </>
                  )}
                  <Button $size="sm" $variant="ghost" disabled={busy} style={{ color: colors.red.main }}
                    onClick={() => { const p = packages.find((x) => x.id === detail.id); if (p) void deletePkg(p) }}>
                    <Trash2 size={13} /> {t('common.delete', 'Delete')}
                  </Button>
                </span>
              </DetailHead>
            )}
            {!detail ? <Sub>{t('common.loading', 'Loading…')}</Sub> : detail.entries.length === 0 ? (
              <Sub>{t('settings.changes.noEntries', 'No changes captured in this package yet.')}</Sub>
            ) : grouped.map(([entity, entries]) => {
              const groupOpen = openGroups.has(entity)
              // Distinct affected rows (the screen's key columns) — surfaced on the group head so a
              // COLLAPSED group still tells you which records it touches without expanding.
              const keys = [...new Set(entries.map((e) =>
                e.entity_key ? Object.values(e.entity_key).map(fmt).join('·') : '—'))]
              const keysLabel = keys.slice(0, 5).join(', ') + (keys.length > 5 ? ` +${keys.length - 5}` : '')
              return (
              <Group key={entity}>
                <GroupToggleHead onClick={() => toggle(openGroups, setOpenGroups, entity)}>
                  {groupOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  <Layers size={13} style={{ verticalAlign: -2 }} />
                  <span style={{ textTransform: 'capitalize' }}>{entity}</span>
                  <span className="count">{entries.length}</span>
                  {!groupOpen && <span className="keys">{keysLabel}</span>}
                </GroupToggleHead>
                {groupOpen && entries.map((e) => {
                  const oldV = e.old_values ?? {}; const newV = e.new_values ?? {}
                  const { fields, changed, unchanged } = classifyFields(oldV, newV)
                  const excluded = e.status === 'excluded'
                  const draft = detail?.status === 'draft'
                  const open = openEntries.has(e.id)
                  const showUnch = showUnchanged.has(e.id)
                  const isInvocation = INVOCATION_OPS.has(e.operation)
                  const visibleFields = showUnch ? fields : changed
                  const summary = isInvocation
                    ? t('settings.changes.replaysOnApply', 'replays on apply')
                    : e.operation === 'DELETE'
                      ? t('settings.changes.rowRemoved', 'row removed')
                      : e.operation === 'INSERT'
                        ? t('settings.changes.nFields', '{{n}} field(s)', { n: changed.length })
                        : t('settings.changes.nChanged', '{{n}} changed', { n: changed.length })
                  return (
                    <Row key={e.id} style={excluded ? { opacity: 0.5 } : undefined}>
                      <div className="head clickable" onClick={() => toggle(openEntries, setOpenEntries, e.id)}>
                        {open ? <ChevronDown size={13} className="chev" /> : <ChevronRight size={13} className="chev" />}
                        <Badge $tone={OP_TONE[e.operation] ?? colors.text.muted}>{e.operation.replace('_', ' ')}</Badge>
                        <span className="key" style={excluded ? { textDecoration: 'line-through' } : undefined}>{e.entity_key ? Object.entries(e.entity_key).map(([k, v]) => `${k}=${fmt(v)}`).join(' · ') : '—'}</span>
                        <span className="count">{summary}</span>
                        <span className="q">{e.connector}.{e.query}</span>
                        {draft && (
                          <ExBtn disabled={busy} onClick={(ev) => { ev.stopPropagation(); void act(`/entries/${encodeURIComponent(e.id)}/${excluded ? 'include' : 'exclude'}`) }}>
                            {excluded ? <><Eye size={11} /> {t('settings.changes.include', 'Include')}</> : <><EyeOff size={11} /> {t('settings.changes.exclude', 'Exclude')}</>}
                          </ExBtn>
                        )}
                      </div>
                      {open && (
                      <Diff style={{ marginTop: 6 }}>
                        {isInvocation && (
                          <div style={{ gridColumn: '1 / -1', color: colors.orange.main, fontSize: fontSize.micro, marginBottom: 2 }}>
                            {t('settings.changes.replayNote', 'Re-runs this call on apply — its effects can’t be drift-checked. Arguments:')}
                          </div>
                        )}
                        {visibleFields.map((f) => {
                          const o = oldV[f]; const n = newV[f]
                          const hasOld = f in oldV; const hasNew = f in newV
                          const changedF = hasOld && hasNew && fmt(o) !== fmt(n)
                          return (
                            <div key={f} style={{ display: 'contents' }}>
                              <span className="f">{f}</span>
                              <span>
                                {hasOld && (changedF || !hasNew) ? <span className="old">{fmt(o)}</span> : null}
                                {changedF ? <ArrowRight size={11} style={{ verticalAlign: -1, margin: '0 4px', color: colors.text.muted }} /> : null}
                                {hasNew && (changedF || !hasOld) ? <span className="new">{fmt(n)}</span> : null}
                                {hasOld && hasNew && !changedF ? <span className="same">{fmt(n)}</span> : null}
                              </span>
                            </div>
                          )
                        })}
                        {unchanged.length > 0 && (
                          <MoreFields onClick={() => toggle(showUnchanged, setShowUnchanged, e.id)}>
                            {showUnch
                              ? t('settings.changes.hideUnchanged', 'hide {{n}} unchanged', { n: unchanged.length })
                              : t('settings.changes.showUnchanged', '+ {{n}} unchanged', { n: unchanged.length })}
                          </MoreFields>
                        )}
                      </Diff>
                      )}
                    </Row>
                  )
                })}
              </Group>
              )
            })}
          </div>
        </Wrap>
      )}
        <PostApplyEditor />
        </>
      )}
    </>
  )
}
