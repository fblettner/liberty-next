// Settings → Package — full deploy workflow: BUILD a ZIP from selected seeds, IMPORT a ZIP
// from another install. The build side: multi-select seeds (screens / menu items /
// dashboards) on the left, the dependency closure preview on the right WITH PER-DEP
// CHECKBOXES (connectors / pools / api_endpoints default-unchecked — operator opts in).
// The import side: pick a ZIP, pick a strategy (merge / overwrite / replace_all), apply.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Search, Download, Upload, Package, FileText, Menu as MenuIcon, LayoutDashboard, AlertCircle, CheckCircle2 } from 'lucide-react'
import { api, ApiError, authHeaders } from '../../api/client'
import type { ScreensDoc, MenusDoc, DashboardsDoc } from '../../types/config'
import { Banner, Button, Card, Centered, Checkbox, SpinnerRing, Mono } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { DEFAULT_EXCLUDED_KINDS, depKey } from './FindDependenciesModal'

type SeedKind = 'screen' | 'menu_item' | 'dashboard'

interface Seed {
  kind: SeedKind
  name: string
  scope?: string
  label?: string
}

interface Dep {
  kind: string
  name: string
  scope: string | null
  reasons: string[]
}

interface ManifestResponse {
  seeds: Array<{ kind: string; name: string; scope: string | null }>
  deps: Dep[]
  missing: Array<{ kind: string; name: string; scope?: string | null; reason?: string | null }>
  warnings: string[]
  by_kind: Record<string, Dep[]>
  counts: Record<string, number>
}

interface ImportReport {
  files: Array<{
    file: string; strategy: string;
    added: string[]; replaced: string[]; skipped: string[];
    preserved_overrides: string[]
    errors: string[]
  }>
  warnings: string[]
  reloaded: boolean
}

const seedKey = (s: Seed) => `${s.kind}:${s.scope ?? ''}:${s.name}`

const KIND_TITLES: Record<string, string> = {
  connector: 'Connectors', pool: 'Pools', query: 'Queries', api_endpoint: 'API endpoints',
  screen: 'Screens', menu_item: 'Menu items', dashboard: 'Dashboards', chart: 'Charts',
  dictionary_entry: 'Dictionary entries', lookup: 'Lookups', sequence: 'Sequences', enum: 'Enums',
}

const KIND_ORDER = [
  'screen', 'menu_item', 'dashboard', 'chart',
  'connector', 'pool', 'query', 'api_endpoint',
  'dictionary_entry', 'lookup', 'sequence', 'enum',
]

type Strategy = 'merge' | 'overwrite' | 'replace_all'

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0;`
const Tabs = styled.div`display: flex; gap: 4px; flex-shrink: 0;`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 30px; padding: 0 14px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  display: inline-flex; align-items: center; gap: 6px;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap;`
const Grid = styled.div`display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; flex: 1; min-height: 0;`
const Panel = styled(Card)`padding: 0; display: flex; flex-direction: column; overflow: hidden; min-height: 0;`
const PanelHeader = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 12px 14px;
  border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary};
  text-transform: uppercase; letter-spacing: 0.06em;
  & svg { color: ${colors.blue.main}; }
  & .count { font-weight: 400; color: ${colors.text.muted}; text-transform: none; letter-spacing: 0; margin-left: 4px; }
  & .toggle { margin-left: auto; color: ${colors.blue.main}; cursor: pointer; background: transparent; border: 0; font-size: ${fontSize.sm}; font-weight: 400; text-transform: none; letter-spacing: 0; }
  & .toggle:hover { text-decoration: underline; }
`
const PanelBody = styled.div`padding: 10px 14px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;`
const Group = styled.div`display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px;`
const GroupTitle = styled.div`
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted};
  padding: 4px 6px; text-transform: uppercase; letter-spacing: 0.07em; font-weight: 600;
  display: flex; align-items: center; gap: 8px;
  & .toggle { margin-left: auto; color: ${colors.blue.main}; cursor: pointer; background: transparent; border: 0; font-size: ${fontSize.sm}; text-transform: none; letter-spacing: 0; }
  & .toggle:hover { text-decoration: underline; }
`
const ItemRow = styled.label<{ $disabled?: boolean }>`
  display: flex; align-items: center; gap: 8px; padding: 4px 6px; cursor: pointer;
  border-radius: ${radius.sm};
  ${({ $disabled }) => $disabled ? `opacity: 0.6;` : ''}
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .label { color: ${colors.text.muted}; font-size: ${fontSize.sm}; margin-left: 6px; }
  & .reason { color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin-left: 6px; }
  &:hover { background: var(--hover-subtle); }
`
const SearchBox = styled.div`
  display: flex; align-items: center; gap: 6px; padding: 6px 10px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  & input { flex: 1; border: 0; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; }
`
const ImportShell = styled.div`display: flex; flex-direction: column; gap: 14px;`
const StrategyRow = styled.div`display: flex; gap: 14px; flex-wrap: wrap; align-items: stretch;`
const StrategyCard = styled.label<{ $active?: boolean }>`
  flex: 1; min-width: 200px; padding: 12px 14px; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  border-radius: ${radius.md}; display: flex; flex-direction: column; gap: 4px;
  & input { display: none; }
  & .name { font-weight: 600; color: ${colors.text.primary}; }
  & .desc { font-size: ${fontSize.sm}; color: ${colors.text.muted}; }
`
const FileDrop = styled.label<{ $hasFile?: boolean }>`
  display: block; padding: 18px; cursor: pointer; text-align: center;
  border: 1px dashed ${({ $hasFile }) => ($hasFile ? colors.green.border : colors.border)};
  background: ${({ $hasFile }) => ($hasFile ? colors.green.bg : colors.bg.input)};
  border-radius: ${radius.md};
  & input { display: none; }
  & .label { color: ${({ $hasFile }) => ($hasFile ? colors.green.main : colors.text.muted)}; }
`
const ReportTable = styled.div`
  display: flex; flex-direction: column; gap: 8px;
  & .row { display: grid; grid-template-columns: 1.2fr 1fr 4fr; gap: 10px; padding: 8px 10px;
    border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; }
  & .file { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & .counts { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; }
  & .counts .add { color: ${colors.green.main}; margin-right: 8px; }
  & .counts .repl { color: ${colors.blue.main}; margin-right: 8px; }
  & .counts .skip { color: ${colors.text.muted}; margin-right: 8px; }
  & .counts .err { color: ${colors.red.main}; }
  & .detail { color: ${colors.text.muted}; font-size: ${fontSize.micro}; overflow: hidden; }
`

export default function PackageBuilder() {
  const { t } = useTranslation()
  const [mode, setMode] = useState<'build' | 'import'>('build')

  return (
    <Shell>
      <Tabs>
        <TabBtn $active={mode === 'build'} onClick={() => setMode('build')}>
          <Download size={13} /> {t('settings.package.buildTab', 'Build package')}
        </TabBtn>
        <TabBtn $active={mode === 'import'} onClick={() => setMode('import')}>
          <Upload size={13} /> {t('settings.package.importTab', 'Import package')}
        </TabBtn>
      </Tabs>
      {mode === 'build' ? <BuildPanel /> : <ImportPanel />}
    </Shell>
  )
}

// ── build panel ───────────────────────────────────────────────────────────────────

function BuildPanel() {
  const { t } = useTranslation()
  const [screens, setScreens] = useState<ScreensDoc | null>(null)
  const [menus, setMenus] = useState<MenusDoc | null>(null)
  const [dashboards, setDashboards] = useState<DashboardsDoc | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selected, setSelected] = useState<Map<string, Seed>>(new Map())
  const [q, setQ] = useState('')

  const [manifest, setManifest] = useState<ManifestResponse | null>(null)
  const [included, setIncluded] = useState<Set<string>>(new Set())
  const [depBusy, setDepBusy] = useState(false)
  const [depError, setDepError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    Promise.all([
      api.get<ScreensDoc>('/admin/config/screens/parsed'),
      api.get<MenusDoc>('/admin/config/menus/parsed').catch((): MenusDoc => ({ path: '', menus: {} })),
      api.get<DashboardsDoc>('/admin/config/dashboards/parsed').catch((): DashboardsDoc => ({ path: '', dashboards: {} })),
    ])
      .then(([s, m, d]) => { setScreens(s); setMenus(m); setDashboards(d) })
      .catch((e) => setLoadError(e instanceof ApiError ? e.message : String(e)))
  }, [])

  useEffect(() => {
    if (selected.size === 0) { setManifest(null); setDepError(null); setIncluded(new Set()); return }
    let cancelled = false
    setDepBusy(true); setDepError(null)
    const body = { seeds: [...selected.values()].map((s) => ({ kind: s.kind, name: s.name, scope: s.scope })) }
    api.post<ManifestResponse>('/admin/find-dependencies', body)
      .then((r) => {
        if (cancelled) return
        setManifest(r)
        // Pre-tick everything except the default-excluded kinds (connector / pool / api_endpoint).
        const init = new Set<string>()
        for (const d of r.deps) if (!DEFAULT_EXCLUDED_KINDS.has(d.kind)) init.add(depKey(d))
        setIncluded(init)
      })
      .catch((e) => { if (!cancelled) { setDepError(e instanceof ApiError ? e.message : String(e)); setManifest(null) } })
      .finally(() => { if (!cancelled) setDepBusy(false) })
    return () => { cancelled = true }
  }, [selected])

  const toggleSeed = (s: Seed, on: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev)
      const k = seedKey(s)
      if (on) next.set(k, s); else next.delete(k)
      return next
    })
  }
  // Bulk seed selection — by kind (all screens / all menu items / all dashboards) or all-of-all
  // ("bundle a release / first install" workflow). Honours the current search filter so the
  // operator can narrow first then "select all" within that narrowed view.
  const toggleSeedBulk = (allSeeds: Seed[], on: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev)
      for (const s of allSeeds) {
        const k = seedKey(s)
        if (on) next.set(k, s); else next.delete(k)
      }
      return next
    })
  }
  const clearSeeds = () => setSelected(new Map())

  const toggleDep = (d: Dep, on: boolean) => {
    setIncluded((prev) => {
      const next = new Set(prev)
      if (on) next.add(depKey(d)); else next.delete(depKey(d))
      return next
    })
  }
  const toggleDepGroup = (kind: string, on: boolean) => {
    const deps = manifest?.by_kind[kind] ?? []
    setIncluded((prev) => {
      const next = new Set(prev)
      for (const d of deps) { if (on) next.add(depKey(d)); else next.delete(depKey(d)) }
      return next
    })
  }
  const selectAllDeps = (on: boolean) => {
    const deps = manifest?.deps ?? []
    setIncluded((prev) => {
      if (!on) return new Set()
      const next = new Set(prev)
      for (const d of deps) next.add(depKey(d))
      return next
    })
  }

  const needle = q.trim().toLowerCase()
  const matches = (s: Seed) => !needle || `${s.scope ?? ''} ${s.name} ${s.label ?? ''}`.toLowerCase().includes(needle)

  const screenSeeds = useMemo<Array<{ app: string; seeds: Seed[] }>>(() => {
    if (!screens) return []
    return Object.entries(screens.screens || {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([app, byId]) => ({
        app,
        seeds: Object.entries(byId).sort(([a], [b]) => a.localeCompare(b))
          .map(([id, s]) => ({ kind: 'screen' as const, name: id, scope: app, label: s.label || undefined })),
      }))
  }, [screens])
  const menuSeeds = useMemo<Array<{ app: string; seeds: Seed[] }>>(() => {
    if (!menus) return []
    return Object.entries(menus.menus || {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([app, am]) => ({
        app,
        seeds: (am.items || []).map((it) => ({
          kind: 'menu_item' as const, name: it.id, scope: app, label: it.label || undefined,
        })),
      }))
  }, [menus])
  const dashboardSeeds = useMemo<Array<{ app: string; seeds: Seed[] }>>(() => {
    if (!dashboards) return []
    return Object.entries(dashboards.dashboards || {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([scope, byId]) => ({
        app: scope,
        seeds: Object.entries(byId).sort(([a], [b]) => a.localeCompare(b))
          .map(([id, d]) => ({ kind: 'dashboard' as const, name: id, label: (d as { label?: string }).label || undefined })),
      }))
  }, [dashboards])

  async function download() {
    if (!manifest || selected.size === 0) return
    setDownloading(true); setDepError(null)
    try {
      const includeList = manifest.deps.filter((d) => included.has(depKey(d)))
        .map((d) => ({ kind: d.kind, scope: d.scope, name: d.name }))
      const res = await fetch('/admin/build-package', {
        method: 'POST',
        headers: { ...authHeaders({ 'Content-Type': 'application/json' }) },
        body: JSON.stringify({
          seeds: [...selected.values()].map((s) => ({ kind: s.kind, name: s.name, scope: s.scope })),
          include: includeList,
        }),
      })
      if (!res.ok) throw new Error(`build-package failed: ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = 'liberty-package.zip'
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setDepError(e instanceof Error ? e.message : String(e))
    } finally { setDownloading(false) }
  }

  if (loadError) return <Banner $tone="error">{loadError}</Banner>
  if (!screens) return <Centered />

  const totalDeps = manifest?.deps.length ?? 0
  const totalIncluded = included.size

  // Flatten + filter the seed pools to the currently-shown set. The "select all" toggles
  // honour the search filter — picking "screens" + "select all" while filtering by "audit"
  // selects only matching screens, not every screen in the install. Same UX the Connectors
  // editor's filter+select pattern uses elsewhere.
  const visibleScreens = screenSeeds.flatMap((g) => g.seeds).filter(matches)
  const visibleMenus = menuSeeds.flatMap((g) => g.seeds).filter(matches)
  const visibleDashboards = dashboardSeeds.flatMap((g) => g.seeds).filter(matches)
  const visibleAll = [...visibleScreens, ...visibleMenus, ...visibleDashboards]
  const isVisible = (s: Seed) => selected.has(seedKey(s))
  const screenPicked = visibleScreens.filter(isVisible).length
  const menuPicked = visibleMenus.filter(isVisible).length
  const dashPicked = visibleDashboards.filter(isVisible).length
  const allPicked = visibleAll.filter(isVisible).length
  const allOn = visibleAll.length > 0 && allPicked === visibleAll.length

  return (
    <>
      <Toolbar>
        <SearchBox style={{ flex: 1 }}>
          <Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={t('settings.package.filter', 'Filter seeds')} />
        </SearchBox>
        <Button $variant="ghost" $size="sm" onClick={clearSeeds} disabled={selected.size === 0}>
          {t('settings.package.clear', 'Clear ({{n}})', { n: selected.size })}
        </Button>
        <Button $variant="primary" $size="sm" onClick={() => void download()}
          disabled={selected.size === 0 || downloading || totalIncluded === 0}>
          {downloading ? <SpinnerRing size={13} thickness={2} /> : <Download size={13} />}
          {t('settings.package.download', 'Download ZIP ({{n}})', { n: totalIncluded })}
        </Button>
      </Toolbar>
      {depError && <Banner $tone="error"><AlertCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />{depError}</Banner>}
      <Grid>
        <Panel>
          <PanelHeader>
            <Package size={14} /> {t('settings.package.seedsTitle', 'Seeds')}
            <span className="count">· {selected.size} {t('settings.package.selected', 'selected')}</span>
            {visibleAll.length > 0 && (
              <button className="toggle" onClick={() => toggleSeedBulk(visibleAll, !allOn)}>
                {allOn ? t('settings.package.deselectAll', 'deselect all') : t('settings.package.selectAll', 'select all')}
              </button>
            )}
          </PanelHeader>
          <PanelBody>
            <SeedGroup title={t('settings.package.screens', 'Screens')} Icon={FileText} groups={screenSeeds} selected={selected}
              toggle={toggleSeed} toggleBulk={(on) => toggleSeedBulk(visibleScreens, on)}
              picked={screenPicked} totalVisible={visibleScreens.length} matches={matches}
              selectAllLabel={t('settings.package.selectAll', 'select all')} deselectAllLabel={t('settings.package.deselectAll', 'deselect all')} />
            <SeedGroup title={t('settings.package.menus', 'Menu items')} Icon={MenuIcon} groups={menuSeeds} selected={selected}
              toggle={toggleSeed} toggleBulk={(on) => toggleSeedBulk(visibleMenus, on)}
              picked={menuPicked} totalVisible={visibleMenus.length} matches={matches}
              selectAllLabel={t('settings.package.selectAll', 'select all')} deselectAllLabel={t('settings.package.deselectAll', 'deselect all')} />
            <SeedGroup title={t('settings.package.dashboards', 'Dashboards')} Icon={LayoutDashboard} groups={dashboardSeeds} selected={selected}
              toggle={toggleSeed} toggleBulk={(on) => toggleSeedBulk(visibleDashboards, on)}
              picked={dashPicked} totalVisible={visibleDashboards.length} matches={matches}
              selectAllLabel={t('settings.package.selectAll', 'select all')} deselectAllLabel={t('settings.package.deselectAll', 'deselect all')} />
          </PanelBody>
        </Panel>
        <Panel>
          <PanelHeader>
            <Package size={14} /> {t('settings.package.depsTitle', 'Dependencies')}
            <span className="count">· {totalIncluded}/{totalDeps} {t('settings.package.willInclude', 'will be packaged')}</span>
            {depBusy && <SpinnerRing size={12} thickness={2} />}
            {manifest && totalDeps > 0 && (
              <button className="toggle" onClick={() => selectAllDeps(totalIncluded !== totalDeps)}>
                {totalIncluded === totalDeps ? t('findDeps.deselectAll', 'deselect all') : t('findDeps.selectAll', 'select all')}
              </button>
            )}
          </PanelHeader>
          <PanelBody>
            {selected.size === 0 && (
              <div style={{ color: colors.text.muted, padding: '12px 4px' }}>
                {t('settings.package.pickHint', 'Tick seeds on the left. The closure of every connector / query / dictionary entry / pool / chart they reach appears here. Connectors / pools / API endpoints are unchecked by default (the target usually has its own) — tick them if you want a bare-metal deploy.')}
              </div>
            )}
            {manifest && manifest.missing.length > 0 && (
              <Banner $tone="error">
                <strong>{manifest.missing.length} {t('settings.package.missing', 'missing references')}</strong>
                <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                  {manifest.missing.slice(0, 8).map((m, i) => (
                    <li key={i}><Mono>{m.kind} · {m.scope ?? '-'} · {m.name}</Mono></li>
                  ))}
                </ul>
              </Banner>
            )}
            {manifest && KIND_ORDER.map((kind) => {
              const deps = manifest.by_kind[kind] ?? []
              if (deps.length === 0) return null
              const groupTicked = deps.filter((d) => included.has(depKey(d))).length
              const allOn = groupTicked === deps.length
              return (
                <Group key={kind}>
                  <GroupTitle>
                    {KIND_TITLES[kind] ?? kind} · {groupTicked}/{deps.length}
                    <button className="toggle" onClick={() => toggleDepGroup(kind, !allOn)}>
                      {allOn ? t('findDeps.deselectAll', 'deselect all') : t('findDeps.selectAll', 'select all')}
                    </button>
                  </GroupTitle>
                  {deps.slice(0, 100).map((d, i) => {
                    const isIn = included.has(depKey(d))
                    return (
                      <ItemRow key={`${kind}-${i}`} $disabled={!isIn}>
                        <Checkbox checked={isIn} onChange={(on) => toggleDep(d, on)} />
                        <span className="name">{d.scope && d.scope !== 'shared' ? `${d.scope}.` : ''}{d.name}</span>
                      </ItemRow>
                    )
                  })}
                  {deps.length > 100 && (
                    <div style={{ color: colors.text.muted, fontSize: fontSize.sm, paddingLeft: 28 }}>
                      … and {deps.length - 100} more (still affected by select-all)
                    </div>
                  )}
                </Group>
              )
            })}
          </PanelBody>
        </Panel>
      </Grid>
    </>
  )
}

// One seed-source section (Screens / Menu items / Dashboards). Reused for each. The
// per-kind "select all" honours the parent's current search filter via `picked` /
// `totalVisible` (the parent already filtered the seed list before counting).
function SeedGroup({
  title, Icon, groups, selected, toggle, toggleBulk, picked, totalVisible, matches,
  selectAllLabel, deselectAllLabel,
}: {
  title: string
  Icon: typeof FileText
  groups: Array<{ app: string; seeds: Seed[] }>
  selected: Map<string, Seed>
  toggle: (s: Seed, on: boolean) => void
  toggleBulk: (on: boolean) => void
  picked: number
  totalVisible: number
  matches: (s: Seed) => boolean
  selectAllLabel: string
  deselectAllLabel: string
}) {
  if (groups.length === 0) return null
  const allOn = totalVisible > 0 && picked === totalVisible
  return (
    <Group>
      <GroupTitle>
        <Icon size={11} style={{ verticalAlign: -1, marginRight: 4 }} />{title}
        <span style={{ color: colors.text.muted, fontSize: fontSize.micro, marginLeft: 6 }}>· {picked}/{totalVisible}</span>
        {totalVisible > 0 && (
          <button className="toggle" onClick={() => toggleBulk(!allOn)}>
            {allOn ? deselectAllLabel : selectAllLabel}
          </button>
        )}
      </GroupTitle>
      {groups.map(({ app, seeds }) => {
        const shown = seeds.filter(matches)
        if (shown.length === 0) return null
        return (
          <Group key={app}>
            <GroupTitle style={{ paddingLeft: 14 }}>{app}</GroupTitle>
            {shown.map((s) => (
              <ItemRow key={seedKey(s)} style={{ paddingLeft: 22 }}>
                <Checkbox checked={selected.has(seedKey(s))} onChange={(on) => toggle(s, on)} />
                <span className="name">{s.name}</span>
                {s.label && <span className="label">— {s.label}</span>}
              </ItemRow>
            ))}
          </Group>
        )
      })}
    </Group>
  )
}

// ── import panel ──────────────────────────────────────────────────────────────────

function ImportPanel() {
  const { t } = useTranslation()
  const [file, setFile] = useState<File | null>(null)
  const [strategy, setStrategy] = useState<Strategy>('overwrite')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<ImportReport | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function apply() {
    if (!file) return
    setBusy(true); setError(null); setReport(null)
    try {
      const form = new FormData()
      form.append('package', file)
      const res = await fetch(`/admin/import-package?strategy=${strategy}`, {
        method: 'POST',
        headers: { ...authHeaders() },   // multipart — fetch sets the Content-Type itself
        body: form,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({} as Record<string, unknown>))
        const message = (body && typeof body === 'object' && 'detail' in body) ? String((body as { detail: unknown }).detail) : `import failed: ${res.status}`
        throw new Error(message)
      }
      const data = await res.json() as { report: ImportReport }
      setReport(data.report)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ImportShell>
      <Banner $tone="info">
        {t('settings.package.importIntro',
          'Apply a package ZIP produced by another install (or this one). The strategy decides what happens when an entity already exists on this install.')}
      </Banner>

      <StrategyRow>
        <StrategyCard $active={strategy === 'merge'}>
          <input type="radio" name="strategy" value="merge" checked={strategy === 'merge'} onChange={() => setStrategy('merge')} />
          <span className="name">{t('settings.package.merge', 'Merge')}</span>
          <span className="desc">{t('settings.package.mergeDesc', 'Add only NEW entities; keep existing ones unchanged. Safest — never overwrites local edits.')}</span>
        </StrategyCard>
        <StrategyCard $active={strategy === 'overwrite'}>
          <input type="radio" name="strategy" value="overwrite" checked={strategy === 'overwrite'} onChange={() => setStrategy('overwrite')} />
          <span className="name">{t('settings.package.overwrite', 'Overwrite (recommended for upgrades)')}</span>
          <span className="desc">{t('settings.package.overwriteDesc', 'Replace existing entities with the package version. Standard upgrade flow — the package is the new truth for the entities it carries.')}</span>
        </StrategyCard>
        <StrategyCard $active={strategy === 'replace_all'}>
          <input type="radio" name="strategy" value="replace_all" checked={strategy === 'replace_all'} onChange={() => setStrategy('replace_all')} />
          <span className="name">{t('settings.package.replaceAll', 'Replace all')}</span>
          <span className="desc">{t('settings.package.replaceAllDesc', 'For each TOML in the ZIP, REPLACE the entire target file. Drops every entity NOT in the package. Brutal — only for "deploy from scratch".')}</span>
        </StrategyCard>
      </StrategyRow>

      <FileDrop $hasFile={!!file} onClick={() => inputRef.current?.click()}>
        <input ref={inputRef} type="file" accept=".zip,application/zip"
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); setReport(null); setError(null) }} />
        <span className="label">
          {file ? (
            <><CheckCircle2 size={16} style={{ verticalAlign: -3, marginRight: 6 }} />{file.name} · {(file.size / 1024).toFixed(1)} KB</>
          ) : (
            <><Upload size={16} style={{ verticalAlign: -3, marginRight: 6 }} />{t('settings.package.pickZip', 'Click to pick a package.zip')}</>
          )}
        </span>
      </FileDrop>

      <Toolbar>
        <Button $variant="primary" $size="sm" onClick={() => void apply()} disabled={!file || busy}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <Upload size={13} />}
          {t('settings.package.apply', 'Apply package')}
        </Button>
        {report?.reloaded && (
          <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>
            <CheckCircle2 size={13} style={{ verticalAlign: -2, marginRight: 4 }} />
            {t('settings.package.reloaded', 'Applied + reloaded')}
          </span>
        )}
      </Toolbar>

      {error && <Banner $tone="error">{error}</Banner>}

      {report && (
        <ReportTable>
          {report.files.map((f) => {
            const overridesCount = (f.preserved_overrides ?? []).length
            return (
              <div className="row" key={f.file}>
                <span className="file">{f.file}</span>
                <span className="counts">
                  <span className="add">+{f.added.length} added</span>
                  <span className="repl">~{f.replaced.length} replaced</span>
                  <span className="skip">⊘{f.skipped.length} skipped</span>
                  {overridesCount > 0 && (
                    <span style={{ color: 'var(--text-warning, #d97706)', marginRight: 8 }}>
                      ⚑{overridesCount} override-preserved
                    </span>
                  )}
                  {f.errors.length > 0 && <span className="err">✗{f.errors.length} errors</span>}
                </span>
                <span className="detail">
                  {(f.added.length + f.replaced.length + f.skipped.length + overridesCount) === 0
                    ? '(no entities)'
                    : [
                        ...f.added.slice(0, 3),
                        ...f.replaced.slice(0, 3),
                        ...f.skipped.slice(0, 3),
                        ...(f.preserved_overrides ?? []).slice(0, 3).map((s) => `[override] ${s}`),
                      ].join(', ')}
                  {f.errors.length > 0 && ` — ERR: ${f.errors.join('; ')}`}
                </span>
              </div>
            )
          })}
          {report.warnings.length > 0 && (
            <Banner $tone="info">
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {report.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </Banner>
          )}
        </ReportTable>
      )}
    </ImportShell>
  )
}
