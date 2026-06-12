// Config version history — browse the snapshot history the server keeps of every TOML save
// (.versions/), diff a stored version against the live file (Monaco side-by-side), restore one
// (writes it back + reloads), or download it. The snapshots are taken server-side on every config
// save (see liberty.versioning); this is the read/compare/restore surface.
//
// Layout: a left rail picks the TOML file (Files mode) or screen (Screens mode); the right panel
// lists that item's versions as collapsible cards — expand one to diff it against live (files) or
// inspect its captured dependency closure (screens).
import '../../services/monaco' // side effect: register Monaco + the worker (stays in the Settings chunk)
import { DiffEditor } from '@monaco-editor/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { RotateCcw, Download, GitCompare, FileText, LayoutGrid, ChevronRight, ChevronDown, Trash2 } from 'lucide-react'
import { api, authHeaders } from '../../api/client'
import { Button, Banner, SpinnerRing, ConfirmModal, Card } from '../../common'
import { useIsLight } from '../../common/useIsLight'
import { colors, fontSize, fonts, radius } from '../../theme'

interface Version {
  id: number; file: string; version: number; size: number; checksum: string
  source: string; comment: string | null; who: string | null; created_at: string
}
interface Bundle { app: string; screen: string; key: string; versions: Version[] }
interface Dep { kind: string; name: string; scope: string | null }
interface BundleManifest {
  by_kind: Record<string, Dep[]>; counts: Record<string, number>
  missing: Array<{ kind: string; name: string; scope?: string | null; reason?: string | null }>
  warnings: string[]
}
interface BundleContents { app: string; screen: string; version: number; manifest: BundleManifest }

const KIND_TITLES: Record<string, string> = {
  connector: 'Connectors', pool: 'Pools', query: 'Queries', api_endpoint: 'API endpoints',
  screen: 'Screens', menu_item: 'Menu items', dashboard: 'Dashboards', chart: 'Charts',
  dictionary_entry: 'Dictionary entries', lookup: 'Lookups', sequence: 'Sequences', enum: 'Enums',
}
const KIND_ORDER = [
  'screen', 'menu_item', 'dashboard', 'chart', 'connector', 'pool', 'query', 'api_endpoint',
  'dictionary_entry', 'lookup', 'sequence', 'enum',
]

// ── left rail: the file / screen picker (kept from the original layout) ──────────────────
const Wrap = styled.div`display: grid; grid-template-columns: 220px 1fr; gap: 16px; align-items: start;`
const Rail = styled.div`display: flex; flex-direction: column; gap: 2px;`
const RailBtn = styled.button<{ $active: boolean }>`
  display: flex; justify-content: space-between; gap: 8px; align-items: center; text-align: left;
  padding: 8px 10px; border-radius: ${radius.md}; cursor: pointer; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  border: 1px solid ${(p) => (p.$active ? colors.blue.main : 'transparent')};
  background: ${(p) => (p.$active ? colors.blue.bg : 'transparent')};
  color: ${(p) => (p.$active ? colors.blue.main : colors.text.primary)};
  &:hover { background: ${colors.bg.input}; }
  .n { color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-family: ${fonts.sans}; }
`

// ── right panel: collapsible version cards (App-tab look & feel) ─────────────────────────
const Stack = styled.div`display: flex; flex-direction: column; gap: 10px; align-items: stretch;`
const Panel = styled(Card)`padding: 0; display: flex; flex-direction: column; overflow: hidden;`
const PanelHead = styled.div<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 4px;
  background: ${(p) => (p.$active ? colors.blue.bg : colors.bg.input)};
  &:hover { background: ${colors.blue.bg}; }
`
const Toggle = styled.button`
  flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; padding: 11px 12px;
  border: 0; background: transparent; cursor: pointer; text-align: left; font-family: ${fonts.sans};
  color: ${colors.text.primary};
  & > .chev { color: ${colors.text.muted}; display: inline-flex; flex-shrink: 0; }
  & > .v { font-family: ${fonts.mono}; font-weight: 600; font-size: ${fontSize.sm}; flex-shrink: 0; }
  & > .meta { color: ${colors.text.secondary}; font-size: ${fontSize.micro}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const Acts = styled.div`display: flex; gap: 4px; padding-right: 8px; flex-shrink: 0;`
const PanelBody = styled.div`border-top: 1px solid ${colors.border};`
const Pill = styled.span`
  display: inline-block; padding: 0 7px; border-radius: 999px; font-size: ${fontSize.micro}; font-family: ${fonts.mono};
  background: ${colors.bg.dropdown}; color: ${colors.text.secondary};
`
const DiffBar = styled.div`
  display: flex; gap: 10px; align-items: center; padding: 6px 12px; background: ${colors.bg.dropdown};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  .l { color: ${colors.red.main}; } .r { color: ${colors.green.main}; }
`
const Tree = styled.div`padding: 10px 14px 14px;`
const Group = styled.div`margin-top: 10px; &:first-of-type { margin-top: 0; }`
const GroupHead = styled.div`
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; text-transform: uppercase; letter-spacing: 0.04em;
  color: ${colors.text.muted}; margin-bottom: 5px;
`
const DepRow = styled.div`
  display: flex; align-items: baseline; gap: 7px; padding: 2px 0; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  .nm { color: ${colors.text.primary}; } .sc { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px; text-align: center;`
const Seg = styled.div`display: inline-flex; gap: 2px; padding: 3px; border-radius: ${radius.md}; background: ${colors.bg.input}; margin-bottom: 14px;`
const SegBtn = styled.button<{ $active: boolean }>`
  display: flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: ${radius.sm}; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; border: none;
  background: ${(p) => (p.$active ? colors.bg.card : 'transparent')};
  color: ${(p) => (p.$active ? colors.text.primary : colors.text.secondary)};
  box-shadow: ${(p) => (p.$active ? '0 1px 2px rgba(0,0,0,0.12)' : 'none')};
  &:hover { color: ${colors.text.primary}; }
`

const fmtDate = (iso: string) => { try { return new Date(iso).toLocaleString() } catch { return iso } }

export default function ConfigVersions() {
  const { t } = useTranslation()
  const light = useIsLight()
  const [mode, setMode] = useState<'files' | 'screens'>('files')
  const [versions, setVersions] = useState<Version[] | null>(null)
  const [bundles, setBundles] = useState<Bundle[] | null>(null)
  const [file, setFile] = useState<string | null>(null)        // selected file (left rail)
  const [bundleKey, setBundleKey] = useState<string | null>(null) // selected screen (left rail)
  const [openId, setOpenId] = useState<number | null>(null)    // expanded version card
  const [fileDiffs, setFileDiffs] = useState<Record<number, { left: string; right: string } | 'loading'>>({})
  const [contents, setContents] = useState<Record<number, BundleContents | 'loading'>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const [confirmV, setConfirmV] = useState<Version | null>(null)
  const [confirmB, setConfirmB] = useState<{ bundle: Bundle; v: Version } | null>(null)
  const [confirmDel, setConfirmDel] = useState<{ kind: 'file' | 'bundle'; v: Version; b?: Bundle } | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ versions: Version[] }>('/admin/config/versions')
      setVersions(r.versions)
      setFile((f) => f ?? (r.versions[0]?.file ?? null))
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }); setVersions([]) }
  }, [])
  const loadBundles = useCallback(async () => {
    try {
      const r = await api.get<{ bundles: Bundle[] }>('/admin/config/screen-versions')
      setBundles(r.bundles)
      setBundleKey((k) => k ?? (r.bundles[0]?.key ?? null))
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }); setBundles([]) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => { if (mode === 'screens' && bundles === null) void loadBundles() }, [mode, bundles, loadBundles])

  // Left-rail entries with version counts.
  const files = useMemo(() => {
    const m = new Map<string, number>()
    for (const v of versions ?? []) m.set(v.file, (m.get(v.file) ?? 0) + 1)
    return [...m.entries()].map(([f, n]) => ({ file: f, n }))
  }, [versions])
  const fileVersions = useMemo(
    () => (versions ?? []).filter((v) => v.file === file).sort((a, b) => b.version - a.version),
    [versions, file],
  )
  const activeBundle = (bundles ?? []).find((b) => b.key === bundleKey) ?? null

  // Expand/collapse a version card, lazily loading its detail (a live diff for files, the captured
  // dependency closure for screens).
  const toggleVersion = useCallback(async (v: Version, kind: 'file' | 'bundle') => {
    if (openId === v.id) { setOpenId(null); return }
    setOpenId(v.id)
    if (kind === 'file') {
      if (fileDiffs[v.id]) return
      setFileDiffs((d) => ({ ...d, [v.id]: 'loading' }))
      try {
        const [left, right] = await Promise.all([
          api.getText(`/admin/config/versions/${v.id}/content`),
          api.getText(`/admin/config/raw?file=${encodeURIComponent(v.file)}`),
        ])
        setFileDiffs((d) => ({ ...d, [v.id]: { left, right } }))
      } catch (e) {
        setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) })
        setFileDiffs((d) => { const n = { ...d }; delete n[v.id]; return n }); setOpenId(null)
      }
    } else {
      if (contents[v.id]) return
      setContents((c) => ({ ...c, [v.id]: 'loading' }))
      try {
        const r = await api.get<BundleContents>(`/admin/config/screen-versions/${v.id}/contents`)
        setContents((c) => ({ ...c, [v.id]: r }))
      } catch (e) {
        setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) })
        setContents((c) => { const n = { ...c }; delete n[v.id]; return n }); setOpenId(null)
      }
    }
  }, [openId, fileDiffs, contents])

  const download = useCallback(async (v: Version) => {
    const res = await fetch(`/admin/config/versions/${v.id}/download`, { headers: authHeaders() })
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const stem = v.file.replace(/\.[^.]+$/, ''); const ext = v.file.slice(stem.length)
    Object.assign(document.createElement('a'), { href: url, download: `${stem}.v${v.version}${ext}` }).click()
    URL.revokeObjectURL(url)
  }, [])
  const downloadBundle = useCallback(async (b: Bundle, v: Version) => {
    const res = await fetch(`/admin/config/screen-versions/${v.id}/download`, { headers: authHeaders() })
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    Object.assign(document.createElement('a'), { href: url, download: `${b.app}_${b.screen}.v${v.version}.zip` }).click()
    URL.revokeObjectURL(url)
  }, [])

  const restore = useCallback(async (v: Version) => {
    setConfirmV(null); setBusy(true); setMsg(null)
    try {
      await api.post(`/admin/config/versions/${v.id}/restore`)
      setMsg({ tone: 'ok', text: t('versions.restored', { file: v.file, v: v.version, defaultValue: 'Restored {{file}} to v{{v}} and reloaded.' }) })
      setOpenId(null); setFileDiffs({}); await load()
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }, [t, load])
  const restoreBundle = useCallback(async (b: Bundle, v: Version) => {
    setConfirmB(null); setBusy(true); setMsg(null)
    try {
      await api.post(`/admin/config/screen-versions/${v.id}/restore`)
      setMsg({ tone: 'ok', text: t('versions.bundleRestored', { app: b.app, screen: b.screen, v: v.version, defaultValue: 'Restored {{app}}.{{screen}} (+ dependencies) to v{{v}} and reloaded.' }) })
      setOpenId(null); await loadBundles()
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }, [t, loadBundles])

  const deleteVersion = useCallback(async (target: { kind: 'file' | 'bundle'; v: Version }) => {
    setConfirmDel(null); setBusy(true); setMsg(null)
    try {
      const base = target.kind === 'bundle' ? '/admin/config/screen-versions' : '/admin/config/versions'
      await api.del(`${base}/${target.v.id}`)
      if (openId === target.v.id) setOpenId(null)
      setMsg({ tone: 'ok', text: t('versions.deleted', { v: target.v.version, defaultValue: 'Deleted v{{v}}.' }) })
      if (target.kind === 'bundle') await loadBundles(); else await load()
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }, [t, load, loadBundles, openId])

  if (versions === null) return <SpinnerRing />

  // One collapsible version card — shared shell, per-kind body + actions.
  const versionCard = (v: Version, kind: 'file' | 'bundle', b?: Bundle) => {
    const open = openId === v.id
    return (
      <Panel key={v.id}>
        <PanelHead $active={open}>
          <Toggle type="button" aria-expanded={open} onClick={() => toggleVersion(v, kind)}>
            <span className="chev">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
            <span className="v">v{v.version}</span>
            <span className="meta">
              {fmtDate(v.created_at)} · <Pill>{v.source}</Pill>{v.who ? ` · ${v.who}` : ''}{v.comment ? ` · ${v.comment}` : ''}
            </span>
          </Toggle>
          <Acts>
            {kind === 'file' ? (
              <>
                <Button $size="sm" $variant="ghost" onClick={() => download(v)} title={t('versions.download', 'Download')}><Download size={13} /></Button>
                <Button $size="sm" $variant="ghost" onClick={() => setConfirmV(v)} disabled={busy} title={t('versions.restore', 'Restore')}><RotateCcw size={13} /></Button>
                <Button $size="sm" $variant="ghost" onClick={() => setConfirmDel({ kind: 'file', v })} disabled={busy} title={t('versions.delete', 'Delete this version')}><Trash2 size={13} /></Button>
              </>
            ) : (
              <>
                <Button $size="sm" $variant="ghost" onClick={() => b && downloadBundle(b, v)} title={t('versions.downloadBundle', 'Download bundle (.zip)')}><Download size={13} /></Button>
                <Button $size="sm" $variant="ghost" onClick={() => b && setConfirmB({ bundle: b, v })} disabled={busy} title={t('versions.restoreBundle', 'Restore screen + dependencies')}><RotateCcw size={13} /></Button>
                <Button $size="sm" $variant="ghost" onClick={() => b && setConfirmDel({ kind: 'bundle', v, b })} disabled={busy} title={t('versions.delete', 'Delete this version')}><Trash2 size={13} /></Button>
              </>
            )}
          </Acts>
        </PanelHead>
        {open && (
          <PanelBody>
            {kind === 'file' ? (() => {
              const d = fileDiffs[v.id]
              if (d === 'loading' || d === undefined) return <SpinnerRing />
              return (
                <>
                  <DiffBar>
                    <GitCompare size={13} />
                    <span><span className="l">v{v.version}</span> → <span className="r">{t('versions.live', 'live')}</span></span>
                  </DiffBar>
                  <DiffEditor
                    height="48vh" language="ini" original={d.left} modified={d.right}
                    theme={light ? 'light' : 'vs-dark'}
                    options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false }}
                  />
                </>
              )
            })() : (() => {
              const c = contents[v.id]
              if (c === 'loading' || c === undefined) return <SpinnerRing />
              const m = c.manifest
              const groups = KIND_ORDER.filter((k) => (m.by_kind[k]?.length ?? 0) > 0)
              if (groups.length === 0 && (m.missing?.length ?? 0) === 0) return <Muted>{t('versions.bundleNoContents', 'No content recorded for this version.')}</Muted>
              return (
                <Tree>
                  {groups.map((kindKey) => (
                    <Group key={kindKey}>
                      <GroupHead>{KIND_TITLES[kindKey] ?? kindKey} · {m.by_kind[kindKey].length}</GroupHead>
                      {m.by_kind[kindKey].map((d, i) => (
                        <DepRow key={`${d.scope ?? ''}:${d.name}:${i}`}>
                          <span className="nm">{d.name}</span>
                          {d.scope && d.scope !== 'shared' ? <span className="sc">{d.scope}</span> : null}
                        </DepRow>
                      ))}
                    </Group>
                  ))}
                  {(m.missing?.length ?? 0) > 0 && (
                    <Group>
                      <GroupHead style={{ color: colors.red.main }}>{t('versions.bundleMissing', 'Missing references')} · {m.missing.length}</GroupHead>
                      {m.missing.map((mm, i) => (
                        <DepRow key={`miss:${mm.name}:${i}`}>
                          <span className="nm" style={{ color: colors.red.main }}>{mm.name}</span>
                          <span className="sc">{mm.kind}{mm.scope ? ` · ${mm.scope}` : ''}{mm.reason ? ` — ${mm.reason}` : ''}</span>
                        </DepRow>
                      ))}
                    </Group>
                  )}
                </Tree>
              )
            })()}
          </PanelBody>
        )}
      </Panel>
    )
  }

  return (
    <div>
      {msg && <Banner $tone={msg.tone === 'ok' ? 'ok' : 'error'}>{msg.text}</Banner>}
      <Seg>
        <SegBtn $active={mode === 'files'} onClick={() => { setMode('files'); setOpenId(null) }}>
          <FileText size={13} /> {t('versions.modeFiles', 'Files')}
        </SegBtn>
        <SegBtn $active={mode === 'screens'} onClick={() => { setMode('screens'); setOpenId(null) }}>
          <LayoutGrid size={13} /> {t('versions.modeScreens', 'Screens')}
        </SegBtn>
      </Seg>

      {mode === 'screens' ? (
        bundles === null ? <SpinnerRing /> : bundles.length === 0 ? (
          <Muted>{t('versions.bundleEmpty', 'No screen history yet — a screen + its dependencies are snapshotted the next time you save it in the Designer.')}</Muted>
        ) : (
          <Wrap>
            <Rail>
              {bundles.map((b) => (
                <RailBtn key={b.key} $active={b.key === bundleKey} onClick={() => { setBundleKey(b.key); setOpenId(null) }}>
                  <span>{b.app}.{b.screen}</span><span className="n">{b.versions.length}</span>
                </RailBtn>
              ))}
            </Rail>
            <Stack>
              {(activeBundle?.versions ?? []).map((v) => versionCard(v, 'bundle', activeBundle ?? undefined))}
            </Stack>
          </Wrap>
        )
      ) : files.length === 0 ? (
        <Muted>{t('versions.empty', 'No config history yet — versions are captured the next time you save a config file.')}</Muted>
      ) : (
        <Wrap>
          <Rail>
            {files.map((f) => (
              <RailBtn key={f.file} $active={f.file === file} onClick={() => { setFile(f.file); setOpenId(null) }}>
                <span>{f.file}</span><span className="n">{f.n}</span>
              </RailBtn>
            ))}
          </Rail>
          <Stack>
            {fileVersions.map((v) => versionCard(v, 'file'))}
          </Stack>
        </Wrap>
      )}

      {confirmV && (
        <ConfirmModal
          title={t('versions.restoreTitle', 'Restore config version')}
          message={t('versions.restoreMsg', { file: confirmV.file, v: confirmV.version, defaultValue: 'Write {{file}} back to v{{v}} and reload? The current file is snapshotted first, so this is undoable.' })}
          confirmLabel={t('versions.restore', 'Restore')}
          onConfirm={() => restore(confirmV)}
          onCancel={() => setConfirmV(null)}
        />
      )}
      {confirmB && (
        <ConfirmModal
          title={t('versions.restoreBundleTitle', 'Restore screen + dependencies')}
          message={t('versions.restoreBundleMsg', { app: confirmB.bundle.app, screen: confirmB.bundle.screen, v: confirmB.v.version, defaultValue: 'Re-apply {{app}}.{{screen}} and its captured dependencies at v{{v}}? All config is snapshotted first (undoable) and entities you marked override = true are preserved.' })}
          confirmLabel={t('versions.restore', 'Restore')}
          onConfirm={() => restoreBundle(confirmB.bundle, confirmB.v)}
          onCancel={() => setConfirmB(null)}
        />
      )}
      {confirmDel && (
        <ConfirmModal
          variant="danger"
          title={t('versions.deleteTitle', 'Delete version')}
          message={confirmDel.kind === 'bundle'
            ? t('versions.deleteBundleMsg', { app: confirmDel.b?.app, screen: confirmDel.b?.screen, v: confirmDel.v.version, defaultValue: 'Permanently delete the {{app}}.{{screen}} bundle v{{v}}? This removes the snapshot for good — it cannot be restored afterwards.' })
            : t('versions.deleteMsg', { file: confirmDel.v.file, v: confirmDel.v.version, defaultValue: 'Permanently delete {{file}} v{{v}}? This removes the snapshot for good — it cannot be restored afterwards. The live file is untouched.' })}
          confirmLabel={t('versions.delete', 'Delete')}
          onConfirm={() => deleteVersion({ kind: confirmDel.kind, v: confirmDel.v })}
          onCancel={() => setConfirmDel(null)}
        />
      )}
    </div>
  )
}
