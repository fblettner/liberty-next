// Change packages — every tracked data modification captured into the active package for its
// connector. Master list on the left, per-package detail on the right: entries grouped by entity
// with old→new diffs. The detail header carries the lifecycle actions (submit / approve / reject)
// and each draft entry can be excluded / re-included (cherry-pick).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Layers, ArrowRight, Send, Check, X, EyeOff, Eye, Download, Upload } from 'lucide-react'
import { Banner, Button } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ApplyBundleModal } from './ApplyBundleModal'

type Pkg = {
  id: string; application: string; name: string; status: string; description: string | null
  created_by: string | null; created_at: string | null; approved_by: string | null; approved_at: string | null
  entry_count: number
}
type Entry = {
  id: string; seq: number; connector: string; query: string; operation: string
  entity: string | null; entity_key: Record<string, unknown> | null
  new_values: Record<string, unknown> | null; old_values: Record<string, unknown> | null
  status: string; captured_by: string | null; captured_at: string | null
}
type PkgDetail = Pkg & { entries: Entry[] }

const Wrap = styled.div`display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start;`
const List = styled.div`display: flex; flex-direction: column; gap: 6px;`
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
const Group = styled.div`margin-bottom: 14px;`
const GroupHead = styled.div`font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary}; margin: 6px 0; text-transform: capitalize;`
const Row = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 9px 11px; margin-bottom: 6px; background: ${colors.bg.input};
  & .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
  & .key { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .q { font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; }
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
const OP_TONE: Record<string, string> = { INSERT: colors.green.main, UPDATE: colors.blue.main, DELETE: colors.red.main }

function fmt(v: unknown): string { return v === null || v === undefined || v === '' ? '∅' : String(v) }

export default function ChangePackagesBuilder() {
  const { t } = useTranslation()
  const [packages, setPackages] = useState<Pkg[] | null>(null)
  const [selId, setSelId] = useState<string | null>(null)
  const [detail, setDetail] = useState<PkgDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [applyOpen, setApplyOpen] = useState(false)

  const loadList = useCallback(() =>
    api.get<{ packages: Pkg[] }>('/admin/changesets')
      .then((d) => { setPackages(d.packages); if (d.packages[0]) setSelId((s) => s ?? d.packages[0].id) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e))), [])
  const loadDetail = useCallback((id: string) =>
    api.get<PkgDetail>(`/admin/changesets/${encodeURIComponent(id)}`)
      .then(setDetail)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e))), [])

  useEffect(() => { void loadList() }, [loadList])
  useEffect(() => { if (!selId) { setDetail(null); return } void loadDetail(selId) }, [selId, loadDetail])

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
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
        <Sub style={{ flex: 1, margin: 0 }}>{t('settings.changes.hint',
          'Every tracked data change is captured into the active package for its connector. Review the diffs here; approval + promotion to another environment come next.')}</Sub>
        <Button $size="sm" $variant="ghost" onClick={() => setApplyOpen(true)}>
          <Upload size={13} /> {t('settings.changes.applyBundle', 'Apply bundle…')}
        </Button>
      </div>
      {error && <Banner $tone="error">{error}</Banner>}
      {applyOpen && <ApplyBundleModal onClose={() => setApplyOpen(false)} />}
      {packages.length === 0 ? (
        <Sub>{t('settings.changes.empty', 'No change packages yet — edit a record on a change-tracked screen to open one.')}</Sub>
      ) : (
        <Wrap>
          <List>
            {packages.map((p) => (
              <Item key={p.id} $active={p.id === selId} onClick={() => setSelId(p.id)}>
                <span className="name">{p.name}</span>
                <span className="meta">
                  <Badge $tone={STATUS_TONE[p.status] ?? colors.text.muted}>{p.status}</Badge>
                  {' '}{p.application} · {t('settings.changes.nChanges', '{{n}} change(s)', { n: p.entry_count })}
                </span>
              </Item>
            ))}
          </List>
          <div>
            {detail && (
              <DetailHead>
                <span className="title">{detail.name}</span>
                <Badge $tone={STATUS_TONE[detail.status] ?? colors.text.muted}>{detail.status}</Badge>
                {detail.approved_by && <span className="who">{detail.status} · {detail.approved_by}</span>}
                <span className="actions">
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
                    <Button $size="sm" $variant="primary" disabled={busy} onClick={() => void exportPkg()}>
                      <Download size={13} /> {t('settings.changes.export', 'Export bundle')}
                    </Button>
                  )}
                </span>
              </DetailHead>
            )}
            {!detail ? <Sub>{t('common.loading', 'Loading…')}</Sub> : detail.entries.length === 0 ? (
              <Sub>{t('settings.changes.noEntries', 'No changes captured in this package yet.')}</Sub>
            ) : grouped.map(([entity, entries]) => (
              <Group key={entity}>
                <GroupHead><Layers size={13} style={{ verticalAlign: -2, marginRight: 6 }} />{entity}</GroupHead>
                {entries.map((e) => {
                  const oldV = e.old_values ?? {}; const newV = e.new_values ?? {}
                  const fields = [...new Set([...Object.keys(oldV), ...Object.keys(newV)])].sort()
                  const excluded = e.status === 'excluded'
                  const draft = detail?.status === 'draft'
                  return (
                    <Row key={e.id} style={excluded ? { opacity: 0.5 } : undefined}>
                      <div className="head">
                        <Badge $tone={OP_TONE[e.operation] ?? colors.text.muted}>{e.operation}</Badge>
                        <span className="key" style={excluded ? { textDecoration: 'line-through' } : undefined}>{e.entity_key ? Object.entries(e.entity_key).map(([k, v]) => `${k}=${fmt(v)}`).join(' · ') : '—'}</span>
                        <span className="q">{e.connector}.{e.query}</span>
                        {draft && (
                          <ExBtn disabled={busy} onClick={() => void act(`/entries/${encodeURIComponent(e.id)}/${excluded ? 'include' : 'exclude'}`)}>
                            {excluded ? <><Eye size={11} /> {t('settings.changes.include', 'Include')}</> : <><EyeOff size={11} /> {t('settings.changes.exclude', 'Exclude')}</>}
                          </ExBtn>
                        )}
                      </div>
                      <Diff>
                        {fields.map((f) => {
                          const o = oldV[f]; const n = newV[f]
                          const hasOld = f in oldV; const hasNew = f in newV
                          const changed = hasOld && hasNew && fmt(o) !== fmt(n)
                          return (
                            <div key={f} style={{ display: 'contents' }}>
                              <span className="f">{f}</span>
                              <span>
                                {hasOld && (changed || !hasNew) ? <span className="old">{fmt(o)}</span> : null}
                                {changed ? <ArrowRight size={11} style={{ verticalAlign: -1, margin: '0 4px', color: colors.text.muted }} /> : null}
                                {hasNew && (changed || !hasOld) ? <span className="new">{fmt(n)}</span> : null}
                                {hasOld && hasNew && !changed ? <span className="same">{fmt(n)}</span> : null}
                              </span>
                            </div>
                          )
                        })}
                      </Diff>
                    </Row>
                  )
                })}
              </Group>
            ))}
          </div>
        </Wrap>
      )}
    </>
  )
}
