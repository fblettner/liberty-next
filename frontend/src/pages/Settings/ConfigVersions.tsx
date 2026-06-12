// Config version history — browse the snapshot history the server keeps of every TOML save
// (.versions/), diff a stored version against the live file (Monaco side-by-side), restore one
// (writes it back + reloads), or download it. The snapshots are taken server-side on every config
// save (see liberty.versioning); this is the read/compare/restore surface.
import '../../services/monaco' // side effect: register Monaco + the worker (stays in the Settings chunk)
import { DiffEditor } from '@monaco-editor/react'
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { RotateCcw, Download, GitCompare, X, FileText, LayoutGrid, Package, ChevronRight, ChevronDown } from 'lucide-react'
import { api, authHeaders } from '../../api/client'
import { Button, Banner, SpinnerRing, ConfirmModal } from '../../common'
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

const Wrap = styled.div`display: grid; grid-template-columns: 200px 1fr; gap: 16px; align-items: start;`
const Files = styled.div`display: flex; flex-direction: column; gap: 2px;`
const FileBtn = styled.button<{ $active: boolean }>`
  display: flex; justify-content: space-between; gap: 8px; align-items: center; text-align: left;
  padding: 7px 10px; border-radius: ${radius.md}; cursor: pointer; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  border: 1px solid ${(p) => (p.$active ? colors.blue.main : 'transparent')};
  background: ${(p) => (p.$active ? colors.blue.bg : 'transparent')};
  color: ${(p) => (p.$active ? colors.blue.main : colors.text.primary)};
  &:hover { background: ${colors.bg.input}; }
  .n { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const Rows = styled.div`display: flex; flex-direction: column; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const Row = styled.div<{ $active?: boolean }>`
  display: grid; grid-template-columns: 54px 1fr auto; gap: 10px; align-items: center; padding: 7px 12px;
  border-bottom: 1px solid ${colors.border}; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${(p) => (p.$active ? colors.blue.bg : 'transparent')};
  &:last-child { border-bottom: none; }
  .v { font-family: ${fonts.mono}; color: ${colors.text.primary}; font-weight: 600; }
  .meta { color: ${colors.text.secondary}; font-size: ${fontSize.micro}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .acts { display: flex; gap: 4px; }
`
const Pill = styled.span`
  display: inline-block; padding: 0 7px; border-radius: 999px; font-size: ${fontSize.micro}; font-family: ${fonts.mono};
  background: ${colors.bg.input}; color: ${colors.text.secondary};
`
const DiffWrap = styled.div`margin-top: 14px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const DiffBar = styled.div`
  display: flex; gap: 12px; align-items: center; padding: 6px 12px; background: ${colors.bg.dropdown};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  .l { color: ${colors.red.main}; } .r { color: ${colors.green.main}; }
`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px; text-align: center;`
const Detail = styled.div`
  grid-column: 1 / -1; padding: 10px 14px 14px 30px; background: ${colors.bg.dropdown};
  border-bottom: 1px solid ${colors.border};
`
const Group = styled.div`margin-top: 10px; &:first-of-type { margin-top: 2px; }`
const GroupHead = styled.div`
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; text-transform: uppercase; letter-spacing: 0.04em;
  color: ${colors.text.muted}; margin-bottom: 5px;
`
const Dep = styled.div`
  display: flex; align-items: baseline; gap: 7px; padding: 2px 0; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  .nm { color: ${colors.text.primary}; } .sc { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
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
  const [file, setFile] = useState<string | null>(null)
  const [diff, setDiff] = useState<{ left: string; right: string; leftLabel: string; rightLabel: string; activeId: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const [confirmV, setConfirmV] = useState<Version | null>(null)
  // Screen-bundle view: dependency-closure snapshots captured per screen on every save.
  const [bundles, setBundles] = useState<Bundle[] | null>(null)
  const [bundleKey, setBundleKey] = useState<string | null>(null)
  const [confirmB, setConfirmB] = useState<{ bundle: Bundle; v: Version } | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)   // expanded bundle version
  const [contents, setContents] = useState<Record<number, BundleContents | 'loading'>>({})

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

  // Files with history (newest activity first), each with its version count.
  const files = useMemo(() => {
    const m = new Map<string, number>()
    for (const v of versions ?? []) m.set(v.file, (m.get(v.file) ?? 0) + 1)
    return [...m.entries()].map(([f, n]) => ({ file: f, n }))
  }, [versions])
  const rows = useMemo(() => (versions ?? []).filter((v) => v.file === file).sort((a, b) => b.version - a.version), [versions, file])

  const diffVsLive = useCallback(async (v: Version) => {
    setBusy(true); setMsg(null)
    try {
      const [left, right] = await Promise.all([
        api.getText(`/admin/config/versions/${v.id}/content`),
        api.getText(`/admin/config/raw?file=${encodeURIComponent(v.file)}`),
      ])
      setDiff({ left, right, leftLabel: `v${v.version}`, rightLabel: t('versions.live', 'live'), activeId: v.id })
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }, [t])

  const download = useCallback(async (v: Version) => {
    const res = await fetch(`/admin/config/versions/${v.id}/download`, { headers: authHeaders() })
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const stem = v.file.replace(/\.[^.]+$/, ''); const ext = v.file.slice(stem.length)
    Object.assign(document.createElement('a'), { href: url, download: `${stem}.v${v.version}${ext}` }).click()
    URL.revokeObjectURL(url)
  }, [])

  const restore = useCallback(async (v: Version) => {
    setConfirmV(null); setBusy(true); setMsg(null)
    try {
      await api.post(`/admin/config/versions/${v.id}/restore`)
      setMsg({ tone: 'ok', text: t('versions.restored', { file: v.file, v: v.version, defaultValue: 'Restored {{file}} to v{{v}} and reloaded.' }) })
      setDiff(null); await load()
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }, [t, load])

  const downloadBundle = useCallback(async (b: Bundle, v: Version) => {
    const res = await fetch(`/admin/config/screen-versions/${v.id}/download`, { headers: authHeaders() })
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    Object.assign(document.createElement('a'), { href: url, download: `${b.app}_${b.screen}.v${v.version}.zip` }).click()
    URL.revokeObjectURL(url)
  }, [])

  const toggleDetail = useCallback(async (v: Version) => {
    if (openId === v.id) { setOpenId(null); return }
    setOpenId(v.id)
    if (contents[v.id]) return   // cached
    setContents((c) => ({ ...c, [v.id]: 'loading' }))
    try {
      const r = await api.get<BundleContents>(`/admin/config/screen-versions/${v.id}/contents`)
      setContents((c) => ({ ...c, [v.id]: r }))
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }); setContents((c) => { const n = { ...c }; delete n[v.id]; return n }) }
  }, [openId, contents])

  const restoreBundle = useCallback(async (b: Bundle, v: Version) => {
    setConfirmB(null); setBusy(true); setMsg(null)
    try {
      await api.post(`/admin/config/screen-versions/${v.id}/restore`)
      setMsg({ tone: 'ok', text: t('versions.bundleRestored', { app: b.app, screen: b.screen, v: v.version, defaultValue: 'Restored {{app}}.{{screen}} (+ dependencies) to v{{v}} and reloaded.' }) })
      await loadBundles()
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }, [t, loadBundles])

  if (versions === null) return <SpinnerRing />

  const activeBundle = (bundles ?? []).find((b) => b.key === bundleKey) ?? null

  return (
    <div>
      {msg && <Banner $tone={msg.tone === 'ok' ? 'ok' : 'error'}>{msg.text}</Banner>}
      <Seg>
        <SegBtn $active={mode === 'files'} onClick={() => { setMode('files'); setDiff(null) }}>
          <FileText size={13} /> {t('versions.modeFiles', 'Files')}
        </SegBtn>
        <SegBtn $active={mode === 'screens'} onClick={() => { setMode('screens'); setDiff(null) }}>
          <LayoutGrid size={13} /> {t('versions.modeScreens', 'Screens')}
        </SegBtn>
      </Seg>
      {mode === 'screens' ? (
        bundles === null ? <SpinnerRing /> : (bundles.length === 0 ? (
          <Muted>{t('versions.bundleEmpty', 'No screen history yet — a screen + its dependencies are snapshotted the next time you save it in the Designer.')}</Muted>
        ) : (
          <Wrap>
            <Files>
              {bundles.map((b) => (
                <FileBtn key={b.key} $active={b.key === bundleKey} onClick={() => setBundleKey(b.key)}>
                  <span>{b.app}.{b.screen}</span><span className="n">{b.versions.length}</span>
                </FileBtn>
              ))}
            </Files>
            <Rows>
              {(activeBundle?.versions ?? []).map((v) => {
                const c = contents[v.id]
                return (
                  <Fragment key={v.id}>
                    <Row $active={openId === v.id}>
                      <span className="v">
                        <Button $size="sm" $variant="ghost" onClick={() => toggleDetail(v)} title={t('versions.inspect', 'Show contents')} style={{ padding: 0, marginRight: 2 }}>
                          {openId === v.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        </Button>
                        v{v.version}
                      </span>
                      <span className="meta">
                        {fmtDate(v.created_at)} · <Pill><Package size={10} style={{ verticalAlign: '-1px', marginRight: 3 }} />{v.source}</Pill>{v.who ? ` · ${v.who}` : ''}{v.comment ? ` · ${v.comment}` : ''}
                      </span>
                      <span className="acts">
                        <Button $size="sm" $variant="ghost" onClick={() => activeBundle && downloadBundle(activeBundle, v)} title={t('versions.downloadBundle', 'Download bundle (.zip)')}><Download size={13} /></Button>
                        <Button $size="sm" $variant="ghost" onClick={() => activeBundle && setConfirmB({ bundle: activeBundle, v })} disabled={busy} title={t('versions.restoreBundle', 'Restore screen + dependencies')}><RotateCcw size={13} /></Button>
                      </span>
                    </Row>
                    {openId === v.id && (
                      <Detail>
                        {c === 'loading' || c === undefined ? <SpinnerRing /> : (() => {
                          const m = c.manifest
                          const groups = KIND_ORDER.filter((k) => (m.by_kind[k]?.length ?? 0) > 0)
                          if (groups.length === 0 && (m.missing?.length ?? 0) === 0) return <Muted>{t('versions.bundleNoContents', 'No content recorded for this version.')}</Muted>
                          return (
                            <>
                              {groups.map((kind) => (
                                <Group key={kind}>
                                  <GroupHead>{KIND_TITLES[kind] ?? kind} · {m.by_kind[kind].length}</GroupHead>
                                  {m.by_kind[kind].map((d, i) => (
                                    <Dep key={`${d.scope ?? ''}:${d.name}:${i}`}>
                                      <span className="nm">{d.name}</span>
                                      {d.scope && d.scope !== 'shared' ? <span className="sc">{d.scope}</span> : null}
                                    </Dep>
                                  ))}
                                </Group>
                              ))}
                              {(m.missing?.length ?? 0) > 0 && (
                                <Group>
                                  <GroupHead style={{ color: colors.red.main }}>{t('versions.bundleMissing', 'Missing references')} · {m.missing.length}</GroupHead>
                                  {m.missing.map((mm, i) => (
                                    <Dep key={`miss:${mm.name}:${i}`}>
                                      <span className="nm" style={{ color: colors.red.main }}>{mm.name}</span>
                                      <span className="sc">{mm.kind}{mm.scope ? ` · ${mm.scope}` : ''}{mm.reason ? ` — ${mm.reason}` : ''}</span>
                                    </Dep>
                                  ))}
                                </Group>
                              )}
                            </>
                          )
                        })()}
                      </Detail>
                    )}
                  </Fragment>
                )
              })}
            </Rows>
          </Wrap>
        ))
      ) : files.length === 0 ? (
        <Muted>{t('versions.empty', 'No config history yet — versions are captured the next time you save a config file.')}</Muted>
      ) : (
        <Wrap>
          <Files>
            {files.map((f) => (
              <FileBtn key={f.file} $active={f.file === file} onClick={() => { setFile(f.file); setDiff(null) }}>
                <span>{f.file}</span><span className="n">{f.n}</span>
              </FileBtn>
            ))}
          </Files>
          <div>
            <Rows>
              {rows.map((v) => (
                <Row key={v.id} $active={diff?.activeId === v.id}>
                  <span className="v">v{v.version}</span>
                  <span className="meta">
                    {fmtDate(v.created_at)} · <Pill>{v.source}</Pill>{v.who ? ` · ${v.who}` : ''}{v.comment ? ` · ${v.comment}` : ''}
                  </span>
                  <span className="acts">
                    <Button $size="sm" $variant="ghost" onClick={() => diffVsLive(v)} disabled={busy} title={t('versions.diff', 'Compare with live')}><GitCompare size={13} /></Button>
                    <Button $size="sm" $variant="ghost" onClick={() => download(v)} title={t('versions.download', 'Download')}><Download size={13} /></Button>
                    <Button $size="sm" $variant="ghost" onClick={() => setConfirmV(v)} disabled={busy} title={t('versions.restore', 'Restore')}><RotateCcw size={13} /></Button>
                  </span>
                </Row>
              ))}
            </Rows>
            {diff && (
              <DiffWrap>
                <DiffBar>
                  <GitCompare size={14} />
                  <span><span className="l">{diff.leftLabel}</span> → <span className="r">{diff.rightLabel}</span></span>
                  <Button $size="sm" $variant="ghost" onClick={() => setDiff(null)} title={t('common.close', 'Close')} style={{ marginLeft: 'auto' }}>
                    <X size={13} />
                  </Button>
                </DiffBar>
                <DiffEditor
                  height="52vh" language="ini" original={diff.left} modified={diff.right}
                  theme={light ? 'light' : 'vs-dark'}
                  options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false }}
                />
              </DiffWrap>
            )}
          </div>
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
    </div>
  )
}
