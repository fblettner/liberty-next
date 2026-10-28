// Settings → Package — full deploy workflow: BUILD a ZIP from selected seeds, IMPORT a ZIP
// from another install. The build side: multi-select seeds (screens / menu items /
// dashboards) on the left, the dependency closure preview on the right WITH PER-DEP
// CHECKBOXES (connectors / pools / api_endpoints default-unchecked — operator opts in).
// The import side: pick a ZIP, pick a strategy (merge / overwrite / replace_all), apply.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { type LucideIcon, Search, Download, Upload, Package, FileText, Menu as MenuIcon, LayoutDashboard, AlertCircle, CheckCircle2, Rocket, Compass, Workflow, Bell, Zap, Sparkles } from 'lucide-react'
import { api, apiDownload, apiUpload, ApiError } from '../../api/client'
import type { ScreensDoc, MenusDoc, DashboardsDoc, ExplorerDoc, AiDoc } from '../../types/config'
import { Banner, Button, Card, Centered, Checkbox, SpinnerRing, Mono, ViewCards } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { DEFAULT_EXCLUDED_KINDS, KIND_ORDER, KIND_TITLES, depKey } from './FindDependenciesModal'
import { PackagePlanView } from './PackagePlanView'
import { PromoteModal } from './PromoteModal'
import { PromotionPanel } from './PromotionPanel'
import type { PromotionPayload } from '../../types/promotion'
import { pendingChanges } from '../../services/packagePlan'
import type { PackagePlan } from '../../types/packagePlan'

type SeedKind = 'screen' | 'menu_item' | 'dashboard' | 'explorer_model' | 'job' | 'notification_rule' | 'action' | 'ai'

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

const seedKey = (s: Seed) => `${s.kind}:${s.scope ?? ''}:${s.name}`

interface SeedSection { key: SeedKind; title: string; Icon: LucideIcon; groups: Array<{ app: string; seeds: Seed[] }> }

interface ExtraSources {
  explorer: ExplorerDoc['explorer']
  jobs: { id: string; description?: string | null }[]
  notifications: Record<string, { label?: string | null }>
  actions: Record<string, { label?: string | null }>
  ai: AiDoc['ai']
}
const EMPTY_EXTRA: ExtraSources = { explorer: {}, jobs: [], notifications: {}, actions: {}, ai: {} }

type Strategy = 'merge' | 'overwrite' | 'replace_all'

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0;`
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

export default function PackageBuilder() {
  const { t } = useTranslation()
  const [mode, setMode] = useState<'build' | 'import' | 'promotion'>('build')

  return (
    <Shell>
      <ViewCards compact value={mode} onChange={setMode} items={[
        { id: 'build', label: t('settings.package.buildTab'), desc: t('settings.package.buildDesc'), icon: <Download size={15} />, color: colors.blue.main },
        { id: 'import', label: t('settings.package.importTab'), desc: t('settings.package.importDesc'), icon: <Upload size={15} />, color: colors.purple.main },
        { id: 'promotion', label: t('settings.promotion.tab'), desc: t('settings.promotion.tabDesc'), icon: <Rocket size={15} />, color: colors.orange.main },
      ]} />
      {mode === 'build' ? <BuildPanel /> : mode === 'import' ? <ImportPanel /> : <PromotionPanel />}
    </Shell>
  )
}

// ── build panel ───────────────────────────────────────────────────────────────────

function BuildPanel() {
  const { t } = useTranslation()
  const [screens, setScreens] = useState<ScreensDoc | null>(null)
  const [menus, setMenus] = useState<MenusDoc | null>(null)
  const [dashboards, setDashboards] = useState<DashboardsDoc | null>(null)
  // The kinds that aren't reached from a screen: picked on their own (C11).
  const [extra, setExtra] = useState<ExtraSources>(EMPTY_EXTRA)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selected, setSelected] = useState<Map<string, Seed>>(new Map())
  const [q, setQ] = useState('')

  const [manifest, setManifest] = useState<ManifestResponse | null>(null)
  const [included, setIncluded] = useState<Set<string>>(new Set())
  const [depBusy, setDepBusy] = useState(false)
  const [depError, setDepError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  // The payload is fixed when the dialog opens (the selection then), so it previews once.
  const [promote, setPromote] = useState<PromotionPayload | null>(null)

  useEffect(() => {
    Promise.all([
      api.get<ScreensDoc>('/admin/config/screens/parsed'),
      api.get<MenusDoc>('/admin/config/menus/parsed').catch((): MenusDoc => ({ path: '', menus: {} })),
      api.get<DashboardsDoc>('/admin/config/dashboards/parsed').catch((): DashboardsDoc => ({ path: '', dashboards: {} })),
    ])
      .then(([s, m, d]) => { setScreens(s); setMenus(m); setDashboards(d) })
      .catch((e) => setLoadError(e instanceof ApiError ? e.message : String(e)))
    // Optional sources: a section with nothing (or a failing route — nomaflow off) just stays empty.
    Promise.all([
      api.get<ExplorerDoc>('/admin/config/explorer/parsed').then((r) => r.explorer).catch(() => ({})),
      api.get<{ jobs: { id: string; description?: string | null }[] }>('/admin/jobs').then((r) => r.jobs).catch(() => []),
      api.get<{ notifications: Record<string, { label?: string | null }> }>('/admin/config/notifications/parsed').then((r) => r.notifications).catch(() => ({})),
      api.get<{ actions: Record<string, { label?: string | null }> }>('/admin/config/actions/parsed').then((r) => r.actions).catch(() => ({})),
      api.get<AiDoc>('/admin/config/ai/parsed').then((r) => r.ai).catch(() => ({})),
    ]).then(([explorer, jobs, notifications, actions, ai]) => setExtra({ explorer, jobs, notifications, actions, ai }))
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

  // Every seed source as one section: screens / menu items / dashboards (with their closure), then
  // the kinds picked on their own. A section with no entries isn't shown.
  const sections = useMemo<SeedSection[]>(() => {
    const byScope = <T,>(m: Record<string, Record<string, T>> | undefined, kind: SeedKind, label: (v: T) => string | null | undefined, scoped = true) =>
      Object.entries(m ?? {}).sort(([a], [b]) => a.localeCompare(b)).map(([scope, byId]) => ({
        app: scope,
        seeds: Object.entries(byId ?? {}).sort(([a], [b]) => a.localeCompare(b))
          .map(([id, v]) => ({ kind, name: id, scope: scoped ? scope : undefined, label: label(v) || undefined })),
      }))
    const flat = (kind: SeedKind, entries: Array<[string, string | null | undefined]>) =>
      entries.length ? [{ app: '', seeds: entries.sort(([a], [b]) => a.localeCompare(b)).map(([id, label]) => ({ kind, name: id, label: label || undefined })) }] : []
    return [
      { key: 'screen', title: t('settings.package.screens'), Icon: FileText, groups: byScope(screens?.screens, 'screen', (v) => v.label) },
      { key: 'menu_item', title: t('settings.package.menus'), Icon: MenuIcon, groups: Object.entries(menus?.menus ?? {}).sort(([a], [b]) => a.localeCompare(b))
          .map(([app, am]) => ({ app, seeds: (am.items || []).map((it) => ({ kind: 'menu_item' as const, name: it.id, scope: app, label: it.label || undefined })) })) },
      { key: 'dashboard', title: t('settings.package.dashboards'), Icon: LayoutDashboard, groups: byScope(dashboards?.dashboards as Record<string, Record<string, { label?: string }>> | undefined, 'dashboard', (v) => v.label, false) },
      { key: 'explorer_model', title: t('settings.package.explorer'), Icon: Compass, groups: byScope(extra.explorer as Record<string, Record<string, { label?: string | null }>>, 'explorer_model', (v) => v.label) },
      { key: 'job', title: t('settings.package.jobs'), Icon: Workflow, groups: flat('job', extra.jobs.map((j) => [j.id, j.description])) },
      { key: 'notification_rule', title: t('settings.package.notifications'), Icon: Bell, groups: flat('notification_rule', Object.entries(extra.notifications).map(([id, r]) => [id, r.label])) },
      { key: 'action', title: t('settings.package.actions'), Icon: Zap, groups: flat('action', Object.entries(extra.actions).map(([id, a]) => [id, a.label])) },
      { key: 'ai', title: t('settings.package.ai'), Icon: Sparkles, groups: flat('ai', Object.keys(extra.ai).map((app) => [app, null])) },
    ]
  }, [screens, menus, dashboards, extra, t])

  async function download() {
    if (!manifest || selected.size === 0) return
    setDownloading(true); setDepError(null)
    try {
      const includeList = manifest.deps.filter((d) => included.has(depKey(d)))
        .map((d) => ({ kind: d.kind, scope: d.scope, name: d.name }))
      await apiDownload('/admin/build-package', 'liberty-package.zip', { json: {
        seeds: [...selected.values()].map((s) => ({ kind: s.kind, name: s.name, scope: s.scope })),
        include: includeList,
      } })
    } catch (e) {
      setDepError(e instanceof Error ? e.message : String(e))
    } finally { setDownloading(false) }
  }

  if (loadError) return <Banner $tone="error">{loadError}</Banner>
  if (!screens) return <Centered />

  const totalDeps = manifest?.deps.length ?? 0
  const totalIncluded = included.size

  // The seeds each section shows under the current search. The "select all" toggles honour the
  // filter — picking "screens" + "select all" while filtering by "audit" selects only matching
  // screens, not every screen in the install.
  const visibleBy = new Map(sections.map((sec) => [sec.key, sec.groups.flatMap((g) => g.seeds).filter(matches)]))
  const visibleAll = [...visibleBy.values()].flat()
  const isVisible = (s: Seed) => selected.has(seedKey(s))
  const allPicked = visibleAll.filter(isVisible).length
  const allOn = visibleAll.length > 0 && allPicked === visibleAll.length

  return (
    <>
      <Toolbar>
        <SearchBox style={{ flex: 1 }}>
          <Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={t('settings.package.filter')} />
        </SearchBox>
        <Button $variant="ghost" $size="sm" onClick={clearSeeds} disabled={selected.size === 0}>
          {t('settings.package.clear', { n: selected.size })}
        </Button>
        <Button $variant="primary" $size="sm" onClick={() => void download()}
          disabled={selected.size === 0 || downloading || totalIncluded === 0}>
          {downloading ? <SpinnerRing size={13} thickness={2} /> : <Download size={13} />}
          {t('settings.package.download', { n: totalIncluded })}
        </Button>
        <Button $size="sm" onClick={() => manifest && setPromote({
          kind: 'config',
          seeds: [...selected.values()].map((s) => ({ kind: s.kind, name: s.name, scope: s.scope ?? null })),
          include: manifest.deps.filter((d) => included.has(depKey(d))).map((d) => ({ kind: d.kind, scope: d.scope, name: d.name })),
        })} disabled={!manifest || selected.size === 0 || totalIncluded === 0}>
          <Rocket size={13} /> {t('settings.promotion.promoteTo')}
        </Button>
      </Toolbar>
      {promote && <PromoteModal payload={promote} onClose={() => setPromote(null)} />}
      {depError && <Banner $tone="error"><AlertCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />{depError}</Banner>}
      <Grid>
        <Panel>
          <PanelHeader>
            <Package size={14} /> {t('settings.package.seedsTitle')}
            <span className="count">· {selected.size} {t('settings.package.selected')}</span>
            {visibleAll.length > 0 && (
              <button className="toggle" onClick={() => toggleSeedBulk(visibleAll, !allOn)}>
                {allOn ? t('settings.package.deselectAll') : t('settings.package.selectAll')}
              </button>
            )}
          </PanelHeader>
          <PanelBody>
            {sections.map((sec) => {
              const visible = visibleBy.get(sec.key) ?? []
              return (
                <SeedGroup key={sec.key} title={sec.title} Icon={sec.Icon} groups={sec.groups} selected={selected}
                  toggle={toggleSeed} toggleBulk={(on) => toggleSeedBulk(visible, on)}
                  picked={visible.filter(isVisible).length} totalVisible={visible.length} matches={matches}
                  selectAllLabel={t('settings.package.selectAll')} deselectAllLabel={t('settings.package.deselectAll')} />
              )
            })}
          </PanelBody>
        </Panel>
        <Panel>
          <PanelHeader>
            <Package size={14} /> {t('settings.package.depsTitle')}
            <span className="count">· {totalIncluded}/{totalDeps} {t('settings.package.willInclude')}</span>
            {depBusy && <SpinnerRing size={12} thickness={2} />}
            {manifest && totalDeps > 0 && (
              <button className="toggle" onClick={() => selectAllDeps(totalIncluded !== totalDeps)}>
                {totalIncluded === totalDeps ? t('findDeps.deselectAll') : t('findDeps.selectAll')}
              </button>
            )}
          </PanelHeader>
          <PanelBody>
            {selected.size === 0 && (
              <div style={{ color: colors.text.muted, padding: '12px 4px' }}>
                {t('settings.package.pickHint')}
              </div>
            )}
            {manifest && manifest.missing.length > 0 && (
              <Banner $tone="error">
                <strong>{manifest.missing.length} {t('settings.package.missing')}</strong>
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
                      {allOn ? t('findDeps.deselectAll') : t('findDeps.selectAll')}
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
  Icon: LucideIcon
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
            {app && <GroupTitle style={{ paddingLeft: 14 }}>{app}</GroupTitle>}
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
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The preview (a dry run) of the picked file under the picked strategy, then the applied result.
  const [plan, setPlan] = useState<PackagePlan | null>(null)
  const [result, setResult] = useState<PackagePlan | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Picking a file or a strategy previews at once: what the import would do, nothing written.
  useEffect(() => {
    if (!file) { setPlan(null); return }
    let cancelled = false
    setBusy('preview'); setError(null); setResult(null)
    apiUpload<{ report: PackagePlan }>(`/admin/import-package?strategy=${strategy}&dry_run=true`, file, 'package')
      .then((r) => { if (!cancelled) setPlan(r.report) })
      .catch((e) => { if (!cancelled) { setPlan(null); setError(e instanceof Error ? e.message : String(e)) } })
      .finally(() => { if (!cancelled) setBusy(null) })
    return () => { cancelled = true }
  }, [file, strategy])

  async function apply() {
    if (!file) return
    setBusy('apply'); setError(null)
    try {
      const data = await apiUpload<{ report: PackagePlan }>(`/admin/import-package?strategy=${strategy}`, file, 'package')
      setResult(data.report); setPlan(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const pending = plan ? pendingChanges(plan) : 0
  return (
    <ImportShell>
      <Banner $tone="info">
        {t('settings.package.importIntro')}
      </Banner>

      <StrategyRow>
        <StrategyCard $active={strategy === 'merge'}>
          <input type="radio" name="strategy" value="merge" checked={strategy === 'merge'} onChange={() => setStrategy('merge')} />
          <span className="name">{t('settings.package.merge')}</span>
          <span className="desc">{t('settings.package.mergeDesc')}</span>
        </StrategyCard>
        <StrategyCard $active={strategy === 'overwrite'}>
          <input type="radio" name="strategy" value="overwrite" checked={strategy === 'overwrite'} onChange={() => setStrategy('overwrite')} />
          <span className="name">{t('settings.package.overwrite')}</span>
          <span className="desc">{t('settings.package.overwriteDesc')}</span>
        </StrategyCard>
        <StrategyCard $active={strategy === 'replace_all'}>
          <input type="radio" name="strategy" value="replace_all" checked={strategy === 'replace_all'} onChange={() => setStrategy('replace_all')} />
          <span className="name">{t('settings.package.replaceAll')}</span>
          <span className="desc">{t('settings.package.replaceAllDesc')}</span>
        </StrategyCard>
      </StrategyRow>

      <FileDrop $hasFile={!!file} onClick={() => inputRef.current?.click()}>
        <input ref={inputRef} type="file" accept=".zip,application/zip"
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); setError(null) }} />
        <span className="label">
          {file ? (
            <><CheckCircle2 size={16} style={{ verticalAlign: -3, marginRight: 6 }} />{file.name} · {(file.size / 1024).toFixed(1)} KB</>
          ) : (
            <><Upload size={16} style={{ verticalAlign: -3, marginRight: 6 }} />{t('settings.package.pickZip')}</>
          )}
        </span>
      </FileDrop>

      {error && <Banner $tone="error">{error}</Banner>}

      {busy === 'preview' && <Toolbar><SpinnerRing size={13} thickness={2} /> {t('settings.package.previewing')}</Toolbar>}

      {plan && busy !== 'preview' && (
        <>
          <Toolbar>
            <Button $variant="primary" $size="sm" onClick={() => void apply()} disabled={pending === 0 || busy === 'apply'}>
              {busy === 'apply' ? <SpinnerRing size={13} thickness={2} /> : <Upload size={13} />}
              {t('settings.package.applyN', { count: pending })}
            </Button>
            <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>
              {pending === 0 ? t('settings.package.nothingToApply') : t('settings.package.previewHint')}
            </span>
          </Toolbar>
          <PackagePlanView plan={plan} />
        </>
      )}

      {result && (
        <>
          {result.reloaded && (
            <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>
              <CheckCircle2 size={13} style={{ verticalAlign: -2, marginRight: 4 }} />
              {t('settings.package.reloaded')}
            </span>
          )}
          <PackagePlanView plan={result} />
        </>
      )}
    </ImportShell>
  )
}
