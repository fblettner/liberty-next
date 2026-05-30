// "Find dependencies" modal — given a seed entity, walks every config it depends on
// transitively and renders the closure as a grouped tree with PER-DEPENDENCY CHECKBOXES.
// Operator can deselect anything they don't want in the ZIP — by default connectors,
// pools, and API endpoints are unchecked (the target install typically already has its
// own connector / pool / API config; shipping these across environments overwrites that).
//
// Click Download ZIP → builds the package with only the ticked items + the seed itself.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { GitMerge, ArrowUpRight, AlertCircle, Download } from 'lucide-react'
import { api, ApiError, authHeaders } from '../../api/client'
import { Banner, Button, Checkbox, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SpinnerRing, Mono } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { deepLinkToUrl } from './FindUsagesModal'

export interface DependencySeed {
  kind: string
  name: string
  scope?: string
  label?: string
}

interface Dep {
  kind: string
  name: string
  scope: string | null
  config: Record<string, unknown>
  reasons: string[]
}

interface ManifestResponse {
  seeds: DependencySeed[]
  deps: Dep[]
  missing: Array<{ kind: string; name: string; scope?: string | null; reason?: string | null }>
  warnings: string[]
  by_kind: Record<string, Dep[]>
  counts: Record<string, number>
}

const KIND_TITLES: Record<string, string> = {
  connector: 'Connectors',
  pool: 'Pools',
  query: 'Queries',
  api_endpoint: 'API endpoints',
  screen: 'Screens',
  menu_item: 'Menu items',
  dashboard: 'Dashboards',
  chart: 'Charts',
  dictionary_entry: 'Dictionary entries',
  lookup: 'Lookups',
  sequence: 'Sequences',
  enum: 'Enums',
}

const KIND_ORDER = [
  'screen', 'menu_item', 'dashboard', 'chart',
  'connector', 'pool', 'query', 'api_endpoint',
  'dictionary_entry', 'lookup', 'sequence', 'enum',
]

// Defaults: connector / pool / api_endpoint are EXCLUDED by default. They're usually
// already present on the target install (operator may have different credentials,
// pool configuration, etc.). Operator can tick them back in when they really want a
// "bare metal" deployment that ships connector config too.
export const DEFAULT_EXCLUDED_KINDS = new Set(['connector', 'pool', 'api_endpoint'])

const Header = styled(ModalHeader)`
  display: flex; align-items: center; gap: 8px;
  & .count { color: ${colors.text.muted}; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; }
`
const Body = styled(ModalBody)`max-height: min(70vh, 640px); overflow-y: auto;`
const Empty = styled.div`color: ${colors.text.muted}; padding: 16px; text-align: center;`
const Group = styled.div`margin-bottom: 14px; &:last-child { margin-bottom: 0; }`
const GroupHead = styled.div`
  display: flex; align-items: center; gap: 8px;
  font-size: ${fontSize.micro}; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em;
  color: ${colors.text.muted}; padding: 6px 4px;
  & .toggle { color: ${colors.blue.main}; font-weight: 600; text-transform: none; letter-spacing: 0; cursor: pointer; font-size: ${fontSize.sm}; background: transparent; border: 0; padding: 0; }
  & .toggle:hover { text-decoration: underline; }
`
const Row = styled.div<{ $clickable?: boolean; $disabled?: boolean }>`
  display: flex; align-items: flex-start; gap: 8px; width: 100%;
  padding: 6px 8px; margin: 2px 0; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input};
  color: ${colors.text.secondary}; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  ${({ $disabled }) => $disabled ? `opacity: 0.6;` : ''}
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; cursor: ${({ $clickable }) => ($clickable ? 'pointer' : 'default')}; }
  & .name { font-family: ${fonts.mono}; }
  & .reason { color: ${colors.text.muted}; font-size: ${fontSize.micro}; overflow: hidden; text-overflow: ellipsis; }
  & svg { flex-shrink: 0; opacity: 0.55; margin-top: 2px; }
  &:hover { background: ${colors.blue.bg}; border-color: ${colors.blue.border}; }
`

function deepLinkForDep(dep: Dep): Record<string, unknown> | null {
  switch (dep.kind) {
    case 'screen':           return { editor: 'screens', app: dep.scope, screen: dep.name }
    case 'menu_item':        return { editor: 'menus', app: dep.scope, item: dep.name }
    case 'dashboard':        return { editor: 'dashboards', dashboard: dep.name }
    case 'chart':            return { editor: 'charts', scope: dep.scope, chart: dep.name }
    case 'connector':        return { editor: 'connectors', connector: dep.name }
    case 'query':            return { editor: 'connectors', connector: dep.scope, query: dep.name }
    case 'pool':             return { editor: 'pools', pool: dep.name }
    case 'dictionary_entry': return { editor: 'dictionary', kind: 'entries', scope: dep.scope, name: dep.name }
    case 'lookup':           return { editor: 'dictionary', kind: 'lookups', scope: dep.scope, name: dep.name }
    case 'sequence':         return { editor: 'dictionary', kind: 'sequences', scope: dep.scope, name: dep.name }
    case 'enum':             return { editor: 'dictionary', kind: 'enums', scope: dep.scope, name: dep.name }
    default:                 return null
  }
}

// Stable identity key for a dependency — kind + scope + name. Used by the include-set so
// tick/untick state survives manifest re-renders.
export const depKey = (d: { kind: string; scope: string | null | undefined; name: string }) =>
  `${d.kind}:${d.scope ?? ''}:${d.name}`

export function FindDependenciesModal({
  seeds, onClose,
}: { seeds: DependencySeed[]; onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [manifest, setManifest] = useState<ManifestResponse | null>(null)
  const [downloading, setDownloading] = useState(false)
  // The include set — every dep in here goes into the ZIP. Seeded with sensible defaults
  // when the manifest arrives (every dep EXCEPT the default-excluded kinds).
  const [included, setIncluded] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null); setManifest(null); setIncluded(new Set())
    api.post<ManifestResponse>('/admin/find-dependencies', {
      seeds: seeds.map((s) => ({ kind: s.kind, name: s.name, scope: s.scope })),
    })
      .then((r) => {
        if (cancelled) return
        setManifest(r)
        // Pre-tick everything except the default-excluded kinds.
        const init = new Set<string>()
        for (const d of r.deps) {
          if (!DEFAULT_EXCLUDED_KINDS.has(d.kind)) init.add(depKey(d))
        }
        setIncluded(init)
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [JSON.stringify(seeds)])

  const groups = useMemo(() => {
    if (!manifest) return []
    return KIND_ORDER
      .map((k) => ({ kind: k, title: KIND_TITLES[k] ?? k, deps: manifest.by_kind[k] ?? [] }))
      .filter((g) => g.deps.length > 0)
  }, [manifest])

  const total = manifest?.deps.length ?? 0
  const totalIncluded = included.size

  const toggleOne = (d: Dep, on: boolean) => {
    setIncluded((prev) => {
      const next = new Set(prev)
      if (on) next.add(depKey(d)); else next.delete(depKey(d))
      return next
    })
  }
  const toggleGroup = (kind: string, on: boolean) => {
    const deps = manifest?.by_kind[kind] ?? []
    setIncluded((prev) => {
      const next = new Set(prev)
      for (const d of deps) {
        if (on) next.add(depKey(d)); else next.delete(depKey(d))
      }
      return next
    })
  }

  async function download() {
    if (!manifest) return
    setDownloading(true); setError(null)
    try {
      // include list = every selected dep, expressed as {kind, scope, name}. The backend
      // walks again from seeds, then filters the closure to only the requested items.
      // Seeds are always implicit (they're sent separately) — but to be safe we also
      // include them in the list so a seed deselected from view still ships.
      const includeList = manifest.deps
        .filter((d) => included.has(depKey(d)))
        .map((d) => ({ kind: d.kind, scope: d.scope, name: d.name }))
      const res = await fetch('/admin/build-package', {
        method: 'POST',
        headers: { ...authHeaders({ 'Content-Type': 'application/json' }) },
        body: JSON.stringify({
          seeds: seeds.map((s) => ({ kind: s.kind, name: s.name, scope: s.scope })),
          include: includeList,
        }),
      })
      if (!res.ok) throw new Error(`build-package failed: ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'liberty-package.zip'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDownloading(false)
    }
  }

  const titleEntity = seeds.length === 1
    ? (seeds[0].label ?? `${seeds[0].kind} ${seeds[0].name}${seeds[0].scope && seeds[0].scope !== 'shared' ? ` (${seeds[0].scope})` : ''}`)
    : t('findDeps.titleMulti', '{{count}} seeds', { count: seeds.length })

  return (
    <Overlay onClick={onClose}>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(720px, 96vw)' }}>
        <Header>
          <GitMerge size={16} />
          <span>{t('findDeps.title', 'Find dependencies')}</span>
          <span className="count">· {titleEntity}</span>
          {manifest && (
            <span className="count" style={{ marginLeft: 'auto' }}>
              {totalIncluded}/{total} {t('findDeps.included', 'included')}
            </span>
          )}
        </Header>
        <Body>
          {loading && <div style={{ padding: 20, textAlign: 'center' }}><SpinnerRing size={16} thickness={2} /></div>}
          {error && <Banner $tone="error"><AlertCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />{error}</Banner>}
          {!loading && !error && manifest && total === 0 && (
            <Empty>{t('findDeps.empty', 'No dependencies resolved — the seed may not exist or be a self-contained leaf.')}</Empty>
          )}
          {!loading && !error && manifest && total > 0 && (
            <>
              {manifest.missing.length > 0 && (
                <Banner $tone="error">
                  <strong>{t('findDeps.missing', '{{count}} missing references', { count: manifest.missing.length })}</strong>
                  <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
                    {manifest.missing.slice(0, 6).map((m, i) => (
                      <li key={i}><Mono>{m.kind} · {m.scope ?? '-'} · {m.name}</Mono>{m.reason ? ` — ${m.reason}` : ''}</li>
                    ))}
                  </ul>
                </Banner>
              )}
              {groups.map((g) => {
                const groupTicked = g.deps.filter((d) => included.has(depKey(d))).length
                const allOn = groupTicked === g.deps.length
                return (
                  <Group key={g.kind}>
                    <GroupHead>
                      {g.title} · {groupTicked}/{g.deps.length}
                      <button className="toggle" onClick={() => toggleGroup(g.kind, !allOn)}>
                        {allOn ? t('findDeps.deselectAll', 'deselect all') : t('findDeps.selectAll', 'select all')}
                      </button>
                    </GroupHead>
                    {g.deps.map((d, i) => {
                      const link = deepLinkForDep(d)
                      const url = link ? deepLinkToUrl(link) : null
                      const isIn = included.has(depKey(d))
                      return (
                        <Row key={`${g.kind}-${i}`} $clickable={!!url} $disabled={!isIn} title={url ?? ''}>
                          <Checkbox checked={isIn} onChange={(on) => toggleOne(d, on)} />
                          <span className="text" onClick={() => { if (url) { navigate(url); onClose() } }}>
                            <span className="name">{d.scope && d.scope !== 'shared' ? `${d.scope}.` : ''}{d.name}</span>
                            {d.reasons.length > 0 && (
                              <span className="reason">{d.reasons[0]}{d.reasons.length > 1 ? ` (+${d.reasons.length - 1} more)` : ''}</span>
                            )}
                          </span>
                          {url && <ArrowUpRight size={14} />}
                        </Row>
                      )
                    })}
                  </Group>
                )
              })}
            </>
          )}
        </Body>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.close', 'Close')}</Button>
          {manifest && totalIncluded > 0 && (
            <Button $size="sm" $variant="primary" onClick={() => void download()} disabled={downloading}>
              {downloading ? <SpinnerRing size={13} thickness={2} /> : <Download size={13} />}
              {t('findDeps.downloadZip', 'Download ZIP ({{n}})', { n: totalIncluded })}
            </Button>
          )}
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export default FindDependenciesModal
