// Developer reference → Impact (C12): the configuration as one graph — every entity (pool, connector,
// query, dictionary item, screen, menu item, dashboard, job…) with what it uses and what uses it,
// followed transitively: the blast radius of a change. The edges are the dependency walker's (the ones
// Package & Deploy follows), so the view shows what a package would carry. A node re-centres the view;
// "Open in editor" goes to its builder.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import {
  ArrowLeft, BarChart3, Bell, BookA, ChevronDown, ChevronRight, Compass, Database, ExternalLink, Globe, Hash,
  LayoutDashboard, LayoutGrid, List, ListOrdered, Menu, Plug, Search, Sparkles, Terminal, Workflow, Zap,
  type LucideIcon,
} from 'lucide-react'
import { Banner, Button, Card, Centered, Checkbox, Tag, type Tone } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ApiError } from '../../api/client'
import {
  entityGroups, entityId, entityLabel, filterEntities, getImpact, getImpactCatalogue, referrerTree, type ReferrerBranch,
} from '../../services/devref'
import type { ImpactCatalogue, ImpactDetail, ImpactNode } from '../../types/devref'
import { deepLinkToUrl } from '../Settings/FindUsagesModal'
import { Body, Main, Side, SideCat, SideItem, SideList, SideSearch } from './styled'

const KIND_ICON: Record<string, LucideIcon> = {
  pool: Database, connector: Plug, query: Terminal, api_endpoint: Globe, dictionary_entry: BookA, lookup: List,
  sequence: Hash, enum: ListOrdered, screen: LayoutGrid, menu_item: Menu, chart: BarChart3, dashboard: LayoutDashboard,
  action: Zap, explorer_model: Compass, job: Workflow, notification_rule: Bell, ai: Sparkles,
}
const KIND_TONE: Record<string, Tone> = {
  pool: 'neutral', connector: 'purple', query: 'blue', api_endpoint: 'blue', dictionary_entry: 'yellow', lookup: 'yellow',
  sequence: 'yellow', enum: 'yellow', screen: 'green', menu_item: 'neutral', chart: 'orange', dashboard: 'orange',
  action: 'purple', explorer_model: 'purple', job: 'red', notification_rule: 'orange', ai: 'purple',
}
// A group longer than this starts folded (the dictionary holds a thousand entries) unless a search is on
const FOLD_OVER = 40

type Ref = Pick<ImpactNode, 'kind' | 'name' | 'scope'>

const Head = styled(Card)`display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  & .title { font-family: ${fonts.mono}; font-size: ${fontSize.lg}; font-weight: 600; color: ${colors.text.primary}; }
  & .counts { color: ${colors.text.muted}; font-size: ${fontSize.sm}; }
  & .grow { flex: 1; }`
const Reach = styled(Card)`display: flex; flex-direction: column; gap: 10px;`
const Title = styled.div`font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};`
const Tiles = styled.div`display: flex; gap: 8px; flex-wrap: wrap;`
const Tile = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input};
  & .n { font-size: ${fontSize.lg}; font-weight: 600; color: ${colors.text.primary}; }
  & .k { font-size: ${fontSize.sm}; color: ${colors.text.secondary}; }
`
const Cols = styled.div`display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; min-height: 0; flex: 1;
  @media (max-width: 1100px) { grid-template-columns: minmax(0, 1fr); }`
const Pane = styled(Card)`display: flex; flex-direction: column; gap: 8px; min-height: 0; overflow: hidden;`
const PaneBody = styled.div`overflow: auto; min-height: 0; flex: 1;`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const Row = styled.div<{ $depth: number }>`
  display: flex; align-items: baseline; gap: 6px; padding: 3px 0 3px ${({ $depth }) => $depth * 18}px; min-width: 0;
  & .label { color: ${colors.text.secondary}; font-size: ${fontSize.sm}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  & .reason { flex: none; color: ${colors.text.muted}; font-size: ${fontSize.micro}; padding: 0 6px; border: 1px solid ${colors.border};
    border-radius: ${radius.sm}; white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
  & .fan { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const Branch = styled.div`border-left: 1px solid ${colors.border}; margin-left: 7px;`
const NodeBtn = styled.button<{ $missing?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; border: none; background: none; padding: 0; cursor: pointer; min-width: 0;
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${({ $missing }) => ($missing ? colors.red.main : colors.text.primary)};
  text-decoration: ${({ $missing }) => ($missing ? 'line-through' : 'none')};
  & svg { flex: none; }
  &:hover { color: ${colors.blue.main}; }
`
const Group = styled.div`display: flex; flex-direction: column; gap: 4px; padding: 6px 0; border-top: 1px solid ${colors.border};
  & .chips { display: flex; gap: 4px; flex-wrap: wrap; }`
const FoldBtn = styled.button`display: flex; align-items: center; gap: 4px; width: 100%; border: none; background: none; padding: 0; cursor: pointer; color: inherit; font: inherit; text-transform: inherit; letter-spacing: inherit;`
const Desc = styled.div`width: 100%; color: ${colors.text.secondary}; font-size: ${fontSize.sm}; line-height: 1.5;`
const Sub = styled.span`width: 100%; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;`
const Filters = styled.div`padding: 8px 12px; border-bottom: 1px solid ${colors.border}; font-size: ${fontSize.sm};`

export default function ImpactView() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [cat, setCat] = useState<ImpactCatalogue | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [unusedOnly, setUnusedOnly] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [history, setHistory] = useState<Ref[]>([])
  const [detail, setDetail] = useState<ImpactDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const current = history[history.length - 1] ?? null

  useEffect(() => {
    getImpactCatalogue().then(setCat).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  useEffect(() => {
    if (!current) { setDetail(null); return }
    let alive = true
    setDetailError(null)
    getImpact(current).then((d) => { if (alive) setDetail(d) })
      .catch((e) => { if (alive) { setDetail(null); setDetailError(e instanceof ApiError ? e.message : String(e)) } })
    return () => { alive = false }
  }, [current])

  const groups = useMemo(() => entityGroups(filterEntities(cat?.entities ?? [], q, unusedOnly), cat?.kinds ?? []), [cat, q, unusedOnly])
  const kindLabel = (k: string) => t(`developer.impactKind.${k}`, { defaultValue: k })
  const kindN = (k: string, count: number) => t(`developer.impactKindN.${k}`, { count, defaultValue: k })
  const focus = (n: Ref) => setHistory((h) => (h.length && entityId(h[h.length - 1]) === entityId(n) ? h : [...h, { kind: n.kind, name: n.name, scope: n.scope }]))
  const pick = (n: Ref) => setHistory([{ kind: n.kind, name: n.name, scope: n.scope }])
  const editorUrl = (n: ImpactNode) => deepLinkToUrl(n.link)

  const node = (n: ImpactNode, extra?: ReactNode) => {
    const Icon = KIND_ICON[n.kind] ?? Database
    return (
      <>
        <NodeBtn type="button" $missing={n.missing} onClick={() => focus(n)}
          title={n.missing ? t('developer.impactMissing') : [`${kindN(n.kind, 1)} · ${entityLabel(n)}`, n.label, n.description].filter(Boolean).join('\n')}>
          <Icon size={13} color={colors.text.muted} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{entityLabel(n)}</span>
          {extra}
        </NodeBtn>
        {n.label && <span className="label">{n.label}</span>}
      </>
    )
  }
  const branch = (b: ReferrerBranch): ReactNode => (
    <div key={entityId(b.node)}>
      <Row $depth={0}>
        {node(b.node)}
        {b.node.fan_in > 1 && <span className="fan" title={t('developer.impactFanIn', { n: b.node.fan_in })}>↑{b.node.fan_in}</span>}
        {b.node.reason && <span className="reason" title={b.node.reason}>{b.node.reason}</span>}
      </Row>
      {b.children.length > 0 && <Branch style={{ paddingLeft: 12 }}>{b.children.map(branch)}</Branch>}
    </div>
  )

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!cat) return <Centered />
  const searching = q.trim() !== '' || unusedOnly
  const tree = detail ? referrerTree(detail.used_by) : []
  const closureGroups = detail ? Object.keys(detail.closure_by_kind).map((k) => ({ kind: k, nodes: detail.closure.filter((c) => c.kind === k) })) : []
  return (
    <Body style={{ gridTemplateColumns: '340px minmax(0, 1fr)' }}>
      <Side>
        <SideSearch>
          <Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('developer.impactSearch')} />
        </SideSearch>
        <Filters>
          <Checkbox checked={unusedOnly} onChange={setUnusedOnly}
            label={t('developer.impactUnusedOnly', { n: cat.entities.filter((e) => e.unused).length })} />
        </Filters>
        <SideList>
          {groups.map((g) => {
            const folded = !searching && (open[g.kind] ?? g.entities.length <= FOLD_OVER) === false
            return (
              <div key={g.kind}>
                <SideCat>
                  <FoldBtn type="button" onClick={() => setOpen((o) => ({ ...o, [g.kind]: folded }))}>
                    {folded ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                    {kindLabel(g.kind)} · {g.entities.length}
                  </FoldBtn>
                </SideCat>
                {!folded && g.entities.map((e) => (
                  <SideItem key={entityId(e)} type="button" $on={current !== null && entityId(current) === entityId(e)} onClick={() => pick(e)}
                    title={[entityLabel(e), e.label, e.description].filter(Boolean).join('\n')} style={{ flexWrap: 'wrap', rowGap: 2 }}>
                    <span className="name" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{e.name}</span>
                    {e.unused ? <span className="gaps">{t('developer.impactUnused')}</span>
                      : <span className="n" title={t('developer.impactUsedByN', { n: e.used_by })}>↑{e.used_by}</span>}
                    {(e.scope || e.label || e.description) && <Sub>{[e.scope, e.label ?? e.description].filter(Boolean).join(' · ')}</Sub>}
                  </SideItem>
                ))}
              </div>
            )
          })}
        </SideList>
      </Side>
      <Main>
        {!current && (
          <Card>
            <Muted>{t('developer.impactPick')}</Muted>
            {cat.broken.length > 0 && (
              <Group style={{ marginTop: 10 }}>
                <Title>{t('developer.impactBroken', { n: cat.broken.length })}</Title>
                <div className="chips">{cat.broken.map((b) => <Tag key={entityId(b)} $tone="red">{kindLabel(b.kind)} · {entityLabel(b)}</Tag>)}</div>
              </Group>
            )}
          </Card>
        )}
        {current && detailError && <Banner $tone="error">{detailError}</Banner>}
        {current && !detail && !detailError && <Centered />}
        {current && detail && (
          <>
            <Head>
              {history.length > 1 && (
                <Button type="button" $variant="ghost" $size="sm" onClick={() => setHistory((h) => h.slice(0, -1))} title={t('developer.impactBack')}>
                  <ArrowLeft size={14} />
                </Button>
              )}
              <Tag $tone={KIND_TONE[detail.entity.kind] ?? 'neutral'}>{kindN(detail.entity.kind, 1)}</Tag>
              <span className="title">{entityLabel(detail.entity)}</span>
              {detail.entity.label && <span style={{ fontWeight: 600, color: colors.text.primary }}>{detail.entity.label}</span>}
              {detail.entity.missing && <Tag $tone="red">{t('developer.impactMissing')}</Tag>}
              <span className="counts">{t('developer.impactCounts', { uses: detail.entity.uses, usedBy: detail.entity.used_by })}</span>
              <span className="grow" />
              {editorUrl(detail.entity) && (
                <Button type="button" $size="sm" onClick={() => { const u = editorUrl(detail.entity); if (u) navigate(u) }}>
                  <ExternalLink size={14} /> {t('developer.impactOpen')}
                </Button>
              )}
              {detail.entity.description && <Desc>{detail.entity.description}</Desc>}
            </Head>
            <Reach>
              <Title>{t('developer.impactReach')}</Title>
              {Object.keys(detail.reach_by_kind).length === 0
                ? <Muted>{detail.entity.kind === 'dictionary_entry' ? t('developer.impactNoneDictionary') : t('developer.impactNone')}</Muted>
                : (
                  <Tiles>
                    {Object.entries(detail.reach_by_kind).map(([k, n]) => {
                      const Icon = KIND_ICON[k] ?? Database
                      return <Tile key={k}><Icon size={16} color={colors.text.muted} /><span className="n">{n}</span><span className="k">{kindN(k, n)}</span></Tile>
                    })}
                  </Tiles>
                )}
              {detail.truncated && <Muted>{t('developer.impactTruncated')}</Muted>}
            </Reach>
            <Cols>
              <Pane>
                <Title>{t('developer.impactUsedBy')}</Title>
                <PaneBody>{tree.length === 0 ? <Muted>—</Muted> : tree.map(branch)}</PaneBody>
              </Pane>
              <Pane>
                <Title>{t('developer.impactUses')}</Title>
                <PaneBody>
                {detail.uses.length === 0 ? <Muted>—</Muted> : detail.uses.map((u) => (
                  <Row key={entityId(u)} $depth={0}>{node(u)}{u.reason && <span className="reason" title={u.reason}>{u.reason}</span>}</Row>
                ))}
                {closureGroups.length > 0 && detail.closure.length > detail.uses.length && (
                  <>
                    <Title style={{ marginTop: 8 }}>{t('developer.impactClosure', { n: detail.closure.length })}</Title>
                    {closureGroups.map((g) => (
                      <Group key={g.kind}>
                        <Muted>{kindLabel(g.kind)} · {g.nodes.length}</Muted>
                        <div className="chips">{g.nodes.map((c) => <Row key={entityId(c)} $depth={0} style={{ padding: '1px 8px 1px 0' }}>{node(c)}</Row>)}</div>
                      </Group>
                    ))}
                  </>
                )}
                </PaneBody>
              </Pane>
            </Cols>
          </>
        )}
      </Main>
    </Body>
  )
}
