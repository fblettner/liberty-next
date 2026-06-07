// Change packages (read-only, Phase 1) — every tracked data modification captured into the
// active package for its connector, ready to review and (later phases) approve + promote. Master
// list on the left, per-package detail on the right: entries grouped by entity with old→new diffs.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Layers, ArrowRight } from 'lucide-react'
import { Banner } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'

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

  useEffect(() => {
    api.get<{ packages: Pkg[] }>('/admin/changesets')
      .then((d) => { setPackages(d.packages); if (d.packages[0]) setSelId((s) => s ?? d.packages[0].id) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])

  useEffect(() => {
    if (!selId) { setDetail(null); return }
    api.get<PkgDetail>(`/admin/changesets/${encodeURIComponent(selId)}`)
      .then(setDetail)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [selId])

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
      <Sub>{t('settings.changes.hint',
        'Every tracked data change is captured into the active package for its connector. Review the diffs here; approval + promotion to another environment come next.')}</Sub>
      {error && <Banner $tone="error">{error}</Banner>}
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
            {!detail ? <Sub>{t('common.loading', 'Loading…')}</Sub> : detail.entries.length === 0 ? (
              <Sub>{t('settings.changes.noEntries', 'No changes captured in this package yet.')}</Sub>
            ) : grouped.map(([entity, entries]) => (
              <Group key={entity}>
                <GroupHead><Layers size={13} style={{ verticalAlign: -2, marginRight: 6 }} />{entity}</GroupHead>
                {entries.map((e) => {
                  const oldV = e.old_values ?? {}; const newV = e.new_values ?? {}
                  const fields = [...new Set([...Object.keys(oldV), ...Object.keys(newV)])].sort()
                  return (
                    <Row key={e.id}>
                      <div className="head">
                        <Badge $tone={OP_TONE[e.operation] ?? colors.text.muted}>{e.operation}</Badge>
                        <span className="key">{e.entity_key ? Object.entries(e.entity_key).map(([k, v]) => `${k}=${fmt(v)}`).join(' · ') : '—'}</span>
                        <span className="q">{e.connector}.{e.query}</span>
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
