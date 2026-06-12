// Config version history — browse the snapshot history the server keeps of every TOML save
// (.versions/), diff a stored version against the live file (Monaco side-by-side), restore one
// (writes it back + reloads), or download it. The snapshots are taken server-side on every config
// save (see liberty.versioning); this is the read/compare/restore surface.
import '../../services/monaco' // side effect: register Monaco + the worker (stays in the Settings chunk)
import { DiffEditor } from '@monaco-editor/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { RotateCcw, Download, GitCompare, X } from 'lucide-react'
import { api, authHeaders } from '../../api/client'
import { Button, Banner, SpinnerRing, ConfirmModal } from '../../common'
import { useIsLight } from '../../common/useIsLight'
import { colors, fontSize, fonts, radius } from '../../theme'

interface Version {
  id: number; file: string; version: number; size: number; checksum: string
  source: string; comment: string | null; who: string | null; created_at: string
}

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

const fmtDate = (iso: string) => { try { return new Date(iso).toLocaleString() } catch { return iso } }

export default function ConfigVersions() {
  const { t } = useTranslation()
  const light = useIsLight()
  const [versions, setVersions] = useState<Version[] | null>(null)
  const [file, setFile] = useState<string | null>(null)
  const [diff, setDiff] = useState<{ left: string; right: string; leftLabel: string; rightLabel: string; activeId: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const [confirmV, setConfirmV] = useState<Version | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ versions: Version[] }>('/admin/config/versions')
      setVersions(r.versions)
      setFile((f) => f ?? (r.versions[0]?.file ?? null))
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }); setVersions([]) }
  }, [])
  useEffect(() => { void load() }, [load])

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

  if (versions === null) return <SpinnerRing />

  return (
    <div>
      {msg && <Banner $tone={msg.tone === 'ok' ? 'ok' : 'error'}>{msg.text}</Banner>}
      {files.length === 0 ? (
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
    </div>
  )
}
