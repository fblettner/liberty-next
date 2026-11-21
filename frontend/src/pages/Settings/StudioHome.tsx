// The Studio — where an application is built, without leaving the page. Pick an application: its map
// shows the menu, the screens, the data each screen uses (queries, endpoints) and the services they call;
// select anything to see what it is linked to; edit it in place — the screen designer, the query editor
// and the service designer open over the map and come back to it. What nothing uses is shown as such.
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import {
  AlertTriangle, ArrowUpRight, Database, FileInput, Folder, Globe, LayoutDashboard, LayoutGrid, Lock, PenLine, Play, Search,
  ShieldAlert, ShieldCheck, Workflow,
} from 'lucide-react'
import { Banner, Button, Centered, Input, ModalBody, ModalHeader, Overlay, SearchSelect, VisualBuilderModal } from '../../common'
import { api, ApiError } from '../../api/client'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius, media } from '../../theme'
import {
  type AppMap, type MapData, type MapScreen, type NodeKey, dataKey, mapLinks, matches, menuKey, reachOf, screenKey, serviceKey, usageOf,
} from '../../services/studioMap'
import ScreensBuilder from './ScreensBuilder'
import { EditQueryModal } from './EditQueryModal'
import { EditEndpointModal } from './EditEndpointModal'
import ActionDesignerModal from './ActionDesignerModal'
import { StudioRunModal, type StudioService } from './StudioModals'

type Tone = 'blue' | 'green' | 'orange' | 'purple'
interface Overview { counts: Record<string, number>; integrity: { errors: number; warnings: number } }
type Editing = { kind: 'screen'; id: string } | { kind: 'query'; connector: string; name: string } | { kind: 'endpoint'; connector: string; name: string }
  | { kind: 'service'; id: string } | { kind: 'run'; service: StudioService } | { kind: 'page'; page: 'menus' | 'dashboards' }

// the editors that are whole pages (the menu, the dashboards): hosted in a window over the map, so the studio is not left
const MenusBuilder = lazy(() => import('./MenusBuilder'))
const DashboardsBuilder = lazy(() => import('./DashboardsBuilder'))

const Page = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1 1 auto; min-height: 0; height: 100%;`
const Bar = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap;`
const Chip = styled.button<{ $tone?: Tone | 'red' | 'muted' }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 999px; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  cursor: ${(p) => (p.onClick ? 'pointer' : 'default')}; white-space: nowrap;
  color: ${(p) => (p.$tone && p.$tone !== 'muted' ? colors[p.$tone].main : colors.text.secondary)};
  background: ${(p) => (p.$tone && p.$tone !== 'muted' ? colors[p.$tone].bg : 'transparent')};
  border: 1px solid ${(p) => (p.$tone && p.$tone !== 'muted' ? colors[p.$tone].border : colors.border)};
  & b { font-weight: 600; color: ${colors.text.primary}; }
`
const Work = styled.div`flex: 1 1 auto; min-height: 420px; display: flex; gap: 12px; ${media.tablet} { flex-direction: column; }`
const MapBox = styled.div`
  position: relative; flex: 1 1 auto; min-width: 0; display: grid; grid-template-columns: 0.8fr 1fr 1.15fr 0.8fr; gap: 28px; padding: 12px;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg}; overflow: hidden;
  background-image: radial-gradient(${colors.border} 1px, transparent 1px); background-size: 18px 18px;
  ${media.tablet} { grid-template-columns: minmax(0, 1fr); overflow: visible; }
`
const Wires = styled.svg`position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 1;`
const Col = styled.div`
  position: relative; z-index: 2; min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 8px;
  & > .head { display: flex; align-items: center; gap: 8px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.secondary}; }
  & > .head .n { margin-left: auto; font-family: ${fonts.mono}; color: ${colors.text.muted}; }
  & > .list { flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding: 2px 2px 12px; scrollbar-width: thin; }
`
const Node = styled.button<{ $tone: Tone; $on: boolean; $rel: boolean; $dim: boolean; $indent?: number }>`
  position: relative; flex: 0 0 auto; display: flex; align-items: center; gap: 8px; text-align: left; cursor: pointer; font-family: ${fonts.sans};
  padding: 7px 9px; margin-left: ${(p) => (p.$indent ?? 0) * 14}px; background: var(--bg-modal); border-radius: ${radius.md};
  border: 1px solid ${(p) => (p.$on ? colors[p.$tone].main : p.$rel ? colors[p.$tone].border : colors.border)};
  opacity: ${(p) => (p.$dim ? 0.3 : 1)}; transition: opacity 0.15s, border-color 0.15s;
  &:hover { border-color: ${(p) => colors[p.$tone].border}; opacity: 1; }
  & .ico { flex: 0 0 auto; display: inline-flex; color: ${(p) => colors[p.$tone].main}; }
  & .txt { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; }
  & .name { font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .sub { font-size: ${fontSize.micro}; font-family: ${fonts.mono}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .tag { flex: 0 0 auto; display: inline-flex; color: ${colors.text.muted}; }
  & .warn { color: ${colors.orange.main}; }
`
const More = styled.button`
  flex: 0 0 auto; padding: 6px 9px; border-radius: ${radius.md}; border: 1px dashed ${colors.border}; background: transparent; cursor: pointer;
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; text-align: left;
  &:hover { color: ${colors.text.primary}; }
`
const Side = styled.div`
  flex: 0 0 340px; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding: 14px 16px;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  ${media.tablet} { flex: 0 0 auto; }
  & h3 { margin: 0; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; overflow-wrap: anywhere; }
  & .kind { font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.muted}; }
  & .mono { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; overflow-wrap: anywhere; }
  & .note { color: ${colors.text.muted}; font-size: ${fontSize.sm}; line-height: 1.5; }
  & .group { display: flex; flex-direction: column; gap: 5px; }
  & .group > span { font-size: ${fontSize.micro}; font-weight: 600; color: ${colors.text.secondary}; }
`
const Jump = styled.button`
  display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: ${radius.sm}; border: 1px solid ${colors.border}; background: transparent;
  cursor: pointer; text-align: left; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
  & em { margin-left: auto; font-style: normal; font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
`

export default function StudioHome() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { focusApp } = useWorkspace()
  const [search] = useSearchParams()
  const [app, setApp] = useState<string | null>(search.get('app') ?? focusApp ?? null)
  const [map, setMap] = useState<AppMap | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sel, setSel] = useState<NodeKey | null>(null)
  const [query, setQuery] = useState('')
  const [unused, setUnused] = useState(false)
  const [editing, setEditing] = useState<Editing | null>(null)

  const load = useCallback((which: string | null) => {
    api.get<AppMap>(`/admin/studio/map${which ? `?app=${encodeURIComponent(which)}` : ''}`)
      .then((m) => { setMap(m); setError(null) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
    api.get<Overview>('/admin/studio/overview').then(setOverview).catch(() => setOverview(null))
  }, [])
  useEffect(() => { load(app) }, [app, load])
  // an editor opened over the map has closed: the map is read again, the selection kept
  const done = useCallback(() => { setEditing(null); load(app) }, [app, load])

  const links = useMemo(() => (map ? mapLinks(map) : []), [map])
  const reach = useMemo(() => reachOf(links, sel), [links, sel])
  const related = reach?.nodes ?? null
  const state = (key: NodeKey) => ({ $on: sel === key, $rel: !!related && related.has(key) && sel !== key, $dim: !!related && !related.has(key) })

  // ── the links of the selection, drawn over the columns ────────────────────────────────
  const box = useRef<HTMLDivElement>(null)
  const nodes = useRef(new Map<NodeKey, HTMLElement>())
  const hold = (key: NodeKey) => (el: HTMLElement | null) => { if (el) nodes.current.set(key, el); else nodes.current.delete(key) }
  const [wires, setWires] = useState<string[]>([])
  const measure = useCallback(() => {
    const frame = box.current?.getBoundingClientRect()
    if (!frame || !sel) { setWires([]); return }
    const out: string[] = []
    for (const [a, b] of reach?.links ?? []) {
      const ra = nodes.current.get(a)?.getBoundingClientRect(), rb = nodes.current.get(b)?.getBoundingClientRect()
      if (!ra || !rb) continue
      const ya = ra.top + ra.height / 2 - frame.top, yb = rb.top + rb.height / 2 - frame.top
      if (Math.abs(ra.left - rb.left) < 4) {                       // the same column (a screen inside another's form): around the right side
        const x = ra.right - frame.left
        out.push(`M ${x} ${ya} C ${x + 26} ${ya}, ${x + 26} ${yb}, ${x} ${yb}`)
      } else {
        const [l, r, yl, yr] = ra.left < rb.left ? [ra, rb, ya, yb] : [rb, ra, yb, ya]
        const x1 = l.right - frame.left, x2 = r.left - frame.left, mid = (x2 - x1) / 2
        out.push(`M ${x1} ${yl} C ${x1 + mid} ${yl}, ${x2 - mid} ${yr}, ${x2} ${yr}`)
      }
    }
    setWires(out)
  }, [reach, sel])
  useLayoutEffect(() => { measure() }, [measure, map, query, unused])
  useEffect(() => {
    const again = () => requestAnimationFrame(measure)
    window.addEventListener('resize', again)
    return () => window.removeEventListener('resize', again)
  }, [measure])
  // what the selection is linked to comes into view, one item per column
  useEffect(() => {
    if (!related || !sel) return
    const seen = new Set<string>()
    for (const key of related) {
      if (key === sel || seen.has(key[0])) continue
      seen.add(key[0])
      nodes.current.get(key)?.scrollIntoView({ block: 'nearest' })
    }
    requestAnimationFrame(measure)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per selection
  }, [sel])

  if (error && !map) return <Banner $tone="error">{error}</Banner>
  if (!map) return <Centered />
  if (!map.app) return <Banner $tone="info">{t('studio.noApp')}</Banner>

  const shownScreens = map.screens.filter((s) => matches(query, s.id, s.label))
  const shownMenu = map.menu.filter((m) => matches(query, m.id, m.label, m.target))
  const usedData = map.data.filter((d) => d.used && matches(query, d.key, d.label))
  const unusedData = map.data.filter((d) => !d.used && matches(query, d.key, d.label))
  const shownServices = map.services.filter((v) => matches(query, v.id, v.label))
  const unreached = map.screens.filter((s) => !s.reached).length
  const issues = (overview?.integrity.errors ?? 0) + (overview?.integrity.warnings ?? 0)
  const pick = (key: NodeKey) => setSel((cur) => (cur === key ? null : key))
  const scroll = { onScroll: () => requestAnimationFrame(measure) }

  const dataNode = (d: MapData) => (
    <Node key={d.key} ref={hold(dataKey(d.key))} type="button" $tone="blue" {...state(dataKey(d.key))} onClick={() => pick(dataKey(d.key))}>
      <span className="ico">{d.kind === 'endpoint' ? <Globe size={15} /> : <Database size={15} />}</span>
      <span className="txt"><span className="name">{d.label || d.name}</span><span className="sub">{d.key}</span></span>
      {d.missing && <span className="tag warn" title={t('studio.missing')}><AlertTriangle size={13} /></span>}
      {d.writable && <span className="tag" title={t('studio.writes')}><PenLine size={13} /></span>}
    </Node>
  )

  // ── the inspector: what is selected, what it is linked to, and its editor ───────────────
  const jump = (key: NodeKey, label: string, how?: string) => <Jump key={key + (how ?? '')} type="button" onClick={() => setSel(key)}>{label}{how && <em>{how}</em>}</Jump>
  const usedBy = (key: string): [MapScreen, string][] => map.screens.flatMap((s) => { const u = usageOf(s, key); return u ? [[s, t(`studio.${u}`)] as [MapScreen, string]] : [] })
  let side: ReactNode
  if (sel?.startsWith('s:')) {
    const s = map.screens.find((x) => screenKey(x.id) === sel)!
    side = s && (
      <>
        <span className="kind">{t('studio.screen')}</span>
        <h3>{s.label}</h3>
        <span className="mono">{map.app}.{s.id}</span>
        {!s.reached && <Banner $tone="warning">{t('studio.unreached')}</Banner>}
        <Bar>
          <Button $variant="primary" $size="sm" onClick={() => setEditing({ kind: 'screen', id: s.id })}><PenLine size={13} /> {t('studio.design')}</Button>
          <Button $variant="ghost" $size="sm" onClick={() => navigate(`/screen/${encodeURIComponent(map.app!)}/${encodeURIComponent(s.id)}`)}><ArrowUpRight size={13} /> {t('studio.open')}</Button>
        </Bar>
        <div className="group"><span>{t('studio.usesData')}</span>
          {[...(s.read ? [[s.read, t('studio.reads')]] : []), ...s.writes.map((k) => [k, t('studio.saves')]), ...s.calls.map((k) => [k, t('studio.calls')])]
            .map(([k, how]) => jump(dataKey(k), k, how))}
        </div>
        {s.services.length > 0 && <div className="group"><span>{t('studio.usesServices')}</span>{s.services.map((v) => jump(serviceKey(v), v))}</div>}
        {s.nested.length > 0 && <div className="group"><span>{t('studio.nestedScreens')}</span>{s.nested.map((n) => jump(screenKey(n), n))}</div>}
      </>
    )
  } else if (sel?.startsWith('d:')) {
    const d = map.data.find((x) => dataKey(x.key) === sel)!
    const by = d ? usedBy(d.key) : []
    side = d && (
      <>
        <span className="kind">{t(d.kind === 'endpoint' ? 'studio.endpoint' : 'studio.query')}</span>
        <h3>{d.label || d.name}</h3>
        <span className="mono">{d.key}</span>
        {d.missing && <Banner $tone="error">{t('studio.missing')}</Banner>}
        <Bar>
          {d.kind === 'query'
            ? <Button $variant="primary" $size="sm" disabled={d.missing} onClick={() => setEditing({ kind: 'query', connector: d.connector, name: d.name })}><PenLine size={13} /> {t('studio.editQuery')}</Button>
            : <Button $variant="primary" $size="sm" disabled={d.missing} onClick={() => setEditing({ kind: 'endpoint', connector: d.connector, name: d.name })}><PenLine size={13} /> {t('studio.editEndpoint')}</Button>}
        </Bar>
        <div className="group"><span>{t('studio.usedBy')}</span>
          {by.length === 0 && <span className="note">{t('studio.usedByNone')}</span>}
          {by.map(([s, how]) => jump(screenKey(s.id), s.label, how))}
        </div>
        {d.service && <div className="group"><span>{t('studio.servedBy')}</span>{jump(serviceKey(d.service), d.service)}</div>}
      </>
    )
  } else if (sel?.startsWith('v:')) {
    const v = map.services.find((x) => serviceKey(x.id) === sel)!
    side = v && (
      <>
        <span className="kind">{t('studio.service')}</span>
        <h3>{v.label || v.id}</h3>
        <span className="mono">{v.id} · {t('settings.actions.summary.steps', { n: v.steps })}</span>
        {v.missing && <Banner $tone="error">{t('studio.missing')}</Banner>}
        <Bar>
          <Button $variant="primary" $size="sm" disabled={v.missing} onClick={() => setEditing({ kind: 'service', id: v.id })}><Workflow size={13} /> {t('studio.designFlow')}</Button>
          <Button $variant="ghost" $size="sm" disabled={v.missing} onClick={() => {
            void api.get<{ services: StudioService[] }>('/admin/studio/services').then((r) => {
              const found = r.services.find((x) => x.id === v.id)
              if (found) setEditing({ kind: 'run', service: found })
            })
          }}><Play size={13} /> {t('settings.studio.run')}</Button>
        </Bar>
        <div className="group"><span>{t('studio.usedBy')}</span>{v.screens.map((id) => jump(screenKey(id), id))}</div>
        {v.endpoints.length > 0 && <div className="group"><span>{t('studio.exposedAs')}</span>{v.endpoints.map((e) => <span key={e.endpoint} className="mono">POST /api/http/{e.connector}/{e.endpoint}</span>)}</div>}
      </>
    )
  } else if (sel?.startsWith('m:')) {
    const m = map.menu.find((x) => menuKey(x.id) === sel)!
    side = m && (
      <>
        <span className="kind">{t('studio.menuEntry')}</span>
        <h3>{m.label}</h3>
        <span className="mono">{m.id}{m.type ? ` · ${m.type}` : ''}</span>
        <Bar><Button $variant="primary" $size="sm" onClick={() => setEditing({ kind: 'page', page: 'menus' })}><PenLine size={13} /> {t('studio.editMenu')}</Button></Bar>
        {m.type === 'screen' && m.target && <div className="group"><span>{t('studio.opens')}</span>{jump(screenKey(m.target), m.target)}</div>}
      </>
    )
  } else {
    side = (
      <>
        <span className="kind">{t('studio.application')}</span>
        <h3>{map.app}</h3>
        <span className="note">{t('studio.lead')}</span>
        <div className="group">
          <span>{t('studio.toLookAt')}</span>
          {unreached > 0 && <span className="note">{t('studio.unreachedCount', { n: unreached })}</span>}
          {map.data.some((d) => !d.used) && <span className="note">{t('studio.unusedCount', { n: map.data.filter((d) => !d.used).length })}</span>}
          {unreached === 0 && !map.data.some((d) => !d.used) && <span className="note">{t('studio.nothingLoose')}</span>}
        </div>
        {map.dashboards.length > 0 && (
          <div className="group"><span>{t('studio.dashboards')}</span>
            {map.dashboards.map((d) => <Jump key={d.id} type="button" onClick={() => setEditing({ kind: 'page', page: 'dashboards' })}><LayoutDashboard size={13} /> {d.label}{!d.in_menu && <em>{t('studio.notInMenu')}</em>}</Jump>)}
          </div>
        )}
      </>
    )
  }

  return (
    <Page>
      <Bar>
        <div style={{ width: 220 }}>
          <SearchSelect value={map.app} options={map.apps.map((a) => ({ value: a, label: a }))} onChange={(v: string | null) => { if (v) { setSel(null); setApp(v) } }} />
        </div>
        <div style={{ position: 'relative', flex: '0 1 280px' }}>
          <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: colors.text.muted }} />
          <Input type="text" value={query} placeholder={t('studio.find')} style={{ paddingLeft: 30 }} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Chip as="span" $tone="muted"><b>{map.screens.length}</b> {t('studio.screens')}</Chip>
        <Chip as="span" $tone="muted"><b>{map.data.filter((d) => d.used).length}</b> {t('studio.data')}</Chip>
        <Chip as="span" $tone="muted"><b>{map.services.length}</b> {t('studio.services')}</Chip>
        {unreached > 0 && <Chip as="span" $tone="orange"><AlertTriangle size={13} /> {t('studio.unreachedCount', { n: unreached })}</Chip>}
        <span style={{ flex: 1 }} />
        {overview && (
          <Chip type="button" $tone={overview.integrity.errors ? 'red' : issues ? 'orange' : 'green'} onClick={() => navigate('/liberty/integrity')}>
            {issues ? <ShieldAlert size={14} /> : <ShieldCheck size={14} />}
            {issues ? t('studio.checkFound', { errors: overview.integrity.errors, warnings: overview.integrity.warnings }) : t('studio.checkClean')}
          </Chip>
        )}
      </Bar>
      <Work>
        <MapBox ref={box}>
          <Wires>{wires.map((d, i) => <path key={i} d={d} fill="none" stroke={colors.blue.main} strokeWidth={1.6} strokeOpacity={0.75} />)}</Wires>
          <Col>
            <div className="head"><Folder size={14} /> {t('studio.menu')}<span className="n">{map.menu.length}</span></div>
            <div className="list" {...scroll}>
              {shownMenu.map((m) => (
                <Node key={m.id} ref={hold(menuKey(m.id))} type="button" $tone="orange" $indent={query ? 0 : m.depth} {...state(menuKey(m.id))} onClick={() => pick(menuKey(m.id))}>
                  <span className="ico">{m.type === 'dashboard' ? <LayoutDashboard size={15} /> : m.type ? <LayoutGrid size={15} /> : <Folder size={15} />}</span>
                  <span className="txt"><span className="name">{m.label}</span></span>
                </Node>
              ))}
            </div>
          </Col>
          <Col>
            <div className="head"><LayoutGrid size={14} /> {t('studio.screens')}<span className="n">{map.screens.length}</span></div>
            <div className="list" {...scroll}>
              {shownScreens.map((s) => (
                <Node key={s.id} ref={hold(screenKey(s.id))} type="button" $tone="green" {...state(screenKey(s.id))} onClick={() => pick(screenKey(s.id))} onDoubleClick={() => setEditing({ kind: 'screen', id: s.id })}>
                  <span className="ico"><LayoutGrid size={15} /></span>
                  <span className="txt"><span className="name">{s.label}</span><span className="sub">{s.id}</span></span>
                  {!s.reached && <span className="tag warn" title={t('studio.unreached')}><AlertTriangle size={13} /></span>}
                  {s.form && <span className="tag" title={t('studio.hasForm')}><FileInput size={13} /></span>}
                  {s.read_only && <span className="tag" title={t('studio.readOnly')}><Lock size={13} /></span>}
                </Node>
              ))}
            </div>
          </Col>
          <Col>
            <div className="head"><Database size={14} /> {t('studio.data')}<span className="n">{usedData.length}</span></div>
            <div className="list" {...scroll}>
              {usedData.map(dataNode)}
              {unusedData.length > 0 && <More type="button" onClick={() => setUnused((x) => !x)}>{t(unused ? 'studio.hideUnused' : 'studio.showUnused', { n: unusedData.length })}</More>}
              {unused && unusedData.map(dataNode)}
            </div>
          </Col>
          <Col>
            <div className="head"><Workflow size={14} /> {t('studio.services')}<span className="n">{map.services.length}</span></div>
            <div className="list" {...scroll}>
              {shownServices.map((v) => (
                <Node key={v.id} ref={hold(serviceKey(v.id))} type="button" $tone="purple" {...state(serviceKey(v.id))} onClick={() => pick(serviceKey(v.id))} onDoubleClick={() => setEditing({ kind: 'service', id: v.id })}>
                  <span className="ico"><Workflow size={15} /></span>
                  <span className="txt"><span className="name">{v.label || v.id}</span><span className="sub">{v.id}</span></span>
                  {v.missing && <span className="tag warn" title={t('studio.missing')}><AlertTriangle size={13} /></span>}
                </Node>
              ))}
              <More type="button" onClick={() => navigate('/liberty/actions')}>{t('studio.allServices')}</More>
            </div>
          </Col>
        </MapBox>
        <Side>{side}</Side>
      </Work>
      {/* the editors, opened over the map: closing one comes back here */}
      {editing?.kind === 'screen' && <ScreensBuilder key={`${map.app}.${editing.id}`} embedded={{ app: map.app, screen: editing.id }} onDone={done} />}
      {editing?.kind === 'query' && <EditQueryModal connector={editing.connector} queryName={editing.name} onClose={done} onSaved={done} />}
      {editing?.kind === 'endpoint' && <EditEndpointModal connector={editing.connector} endpointName={editing.name} onClose={done} onSaved={done} />}
      {editing?.kind === 'page' && (
        <Overlay style={{ zIndex: 500 }}>
          <VisualBuilderModal $fullscreen={false} onClick={(e) => e.stopPropagation()}>
            <ModalHeader>
              <span style={{ flex: 1 }}>{t(editing.page === 'menus' ? 'studio.menu' : 'studio.dashboards')}</span>
              <Button $variant="ghost" $size="sm" onClick={done}>{t('common.close')}</Button>
            </ModalHeader>
            <ModalBody><Suspense fallback={<Centered />}>{editing.page === 'menus' ? <MenusBuilder /> : <DashboardsBuilder />}</Suspense></ModalBody>
          </VisualBuilderModal>
        </Overlay>
      )}
      {editing?.kind === 'service' && <ActionDesignerModal actionId={editing.id} onClose={done} onSaved={() => load(app)} />}
      {editing?.kind === 'run' && <StudioRunModal service={editing.service} onClose={() => setEditing(null)} />}
    </Page>
  )
}
