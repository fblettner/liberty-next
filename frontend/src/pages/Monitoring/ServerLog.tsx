// The Server log (C8): the log file searched — level floor, module, text (message, logger,
// exception, request id), period — newest first; a line opens its exception; a request id
// filters on that request; Export (the matches as text) and Download (every file, zipped); the
// levels per module, changed at run time or saved to app.toml. Superuser routes /admin/logs.
import { useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Download, FileText, RefreshCw, Search } from 'lucide-react'
import { ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, Input, SearchSelect, Tag, type SearchSelectOption } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { download, formatBytes, getLevels, logQuery, searchLog, setLevels, type LogEntry, type LogFilters, type LogLevels, type LogSearch } from '../../services/serverLog'

const FILTER_KEY = 'liberty.serverlog.filters'
const Bar = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px;`
const Spacer = styled.div`flex: 1;`
const Meta = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; margin-bottom: 8px;`
const List = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; overflow: hidden;
`
const Line = styled.div<{ $open: boolean }>`
  display: grid; grid-template-columns: 150px 84px minmax(160px, 260px) minmax(0, 1fr); gap: 10px; align-items: baseline;
  padding: 5px 10px; border-top: 1px solid ${colors.border}; cursor: pointer; color: ${colors.text.secondary};
  &:first-of-type { border-top: none; }
  &:hover { background: var(--hover-subtle); }
  & .msg { color: ${colors.text.primary}; white-space: ${({ $open }) => ($open ? 'pre-wrap' : 'nowrap')}; overflow: hidden; text-overflow: ellipsis; word-break: break-word; }
  & .logger { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: ${colors.text.muted}; }
  & .rid { color: ${colors.blue.main}; cursor: pointer; margin-left: 8px; }
  @media (max-width: 900px) { grid-template-columns: 1fr; gap: 2px; }
`
const Exc = styled.pre`
  grid-column: 1 / -1; margin: 4px 0 2px; padding: 8px; border-radius: ${radius.sm}; background: ${colors.bg.input};
  color: ${colors.red.main}; font-size: ${fontSize.micro}; white-space: pre-wrap; overflow-x: auto;
`
const LevelsGrid = styled.div`display: grid; grid-template-columns: minmax(200px, 1fr) 160px; gap: 6px 12px; align-items: center; font-size: ${fontSize.sm};`

const tone = (level: string) => (level === 'ERROR' || level === 'CRITICAL' ? 'red' : level === 'WARNING' ? 'orange' : level === 'INFO' ? 'blue' : 'neutral') as 'red' | 'orange' | 'blue' | 'neutral'

function readFilters(): LogFilters {
  try { return { level: '', module: '', q: '', hours: '24', ...(JSON.parse(localStorage.getItem(FILTER_KEY) ?? '{}') as Partial<LogFilters>) } } catch { return { level: '', module: '', q: '', hours: '24' } }
}

export default function ServerLog() {
  const { t, i18n } = useTranslation()
  const [f, setF] = useState<LogFilters>(readFilters)
  const [data, setData] = useState<LogSearch | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  const [levels, setLevelsState] = useState<LogLevels | null>(null)
  const [newLogger, setNewLogger] = useState('')
  const [newLevel, setNewLevel] = useState('DEBUG')

  useEffect(() => { try { localStorage.setItem(FILTER_KEY, JSON.stringify(f)) } catch { /* ignore */ } }, [f])
  const load = useCallback(() => {
    setBusy(true)
    searchLog(f).then((r) => { setData(r); setError(null); setOpen(null) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setBusy(false))
  }, [f])
  useEffect(() => { const h = setTimeout(load, 300); return () => clearTimeout(h) }, [load])
  useEffect(() => { getLevels().then(setLevelsState).catch(() => setLevelsState(null)) }, [])

  const levelOpts: SearchSelectOption[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => ({ value: l, label: t('serverLog.atLeast', { level: l }) }))
  const periodOpts: SearchSelectOption[] = [
    { value: '1', label: t('serverLog.lastHour') }, { value: '24', label: t('serverLog.last24h') },
    { value: '168', label: t('serverLog.last7d') }, { value: '0', label: t('serverLog.allKept') },
  ]
  const moduleOpts = useMemo<SearchSelectOption[]>(() => [...new Set([...(data?.modules ?? []), ...(levels?.loggers ?? [])])].sort().map((m) => ({ value: m, label: m, mono: m })), [data, levels])
  const loggerOpts = useMemo<SearchSelectOption[]>(() => (levels?.loggers ?? []).map((m) => ({ value: m, label: m, mono: m })), [levels])
  const choiceOpts: SearchSelectOption[] = (levels?.choices ?? ['NOTSET', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']).map((c) => ({ value: c, label: c === 'NOTSET' ? t('serverLog.inherit') : c }))

  const changeLevel = async (name: string, level: string, persist: boolean) => {
    try { setLevelsState(await setLevels({ [name]: level }, persist)) } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
  }
  const when = (ts: string) => (ts ? new Date(ts).toLocaleString(i18n.language, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '')

  return (
    <>
      <Bar>
        <div style={{ position: 'relative', flex: '1 1 260px', maxWidth: 380 }}>
          <Search size={14} style={{ position: 'absolute', left: 9, top: 9, color: colors.text.muted }} />
          <Input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder={t('serverLog.search')} style={{ paddingLeft: 28 }} />
        </div>
        <div style={{ width: 170 }}><SearchSelect value={f.level} onChange={(v) => setF({ ...f, level: v })} options={levelOpts} anyLabel={t('serverLog.anyLevel')} placeholder={t('serverLog.anyLevel')} /></div>
        <div style={{ width: 210 }}><SearchSelect sort allowCustom value={f.module} onChange={(v) => setF({ ...f, module: v })} options={moduleOpts} anyLabel={t('serverLog.anyModule')} placeholder={t('serverLog.anyModule')} /></div>
        <div style={{ width: 170 }}><SearchSelect value={f.hours} onChange={(v) => setF({ ...f, hours: v || '24' })} options={periodOpts} /></div>
        <Spacer />
        <Button $variant="ghost" $size="sm" onClick={load} disabled={busy}><RefreshCw size={14} /> {t('common.reload')}</Button>
        <Button $variant="ghost" $size="sm" onClick={() => void download(`/admin/logs/export?${logQuery(f)}`, 'liberty-log.txt').catch((e) => setError(String(e)))}><FileText size={14} /> {t('serverLog.export')}</Button>
        <Button $variant="ghost" $size="sm" onClick={() => void download('/admin/logs/download', 'liberty-logs.zip').catch((e) => setError(String(e)))}><Download size={14} /> {t('serverLog.download')}</Button>
      </Bar>
      {error && <Banner $tone="error">{error}</Banner>}
      {data && (
        <Meta>
          {t('serverLog.summary', { n: data.entries.length, scanned: data.scanned })}
          {data.truncated && ` · ${t('serverLog.truncated')}`}
          {' · '}{data.files.map((x) => `${x.name} ${formatBytes(x.size)}`).join(', ')}
        </Meta>
      )}
      {!data && !error ? <Centered /> : data && data.entries.length === 0 ? <Meta>{t('serverLog.none')}</Meta> : data && (
        <List>
          {data.entries.map((e: LogEntry, i) => (
            <Line key={i} $open={open === i} onClick={() => setOpen(open === i ? null : i)}>
              <span>{when(e.ts)}</span>
              <span><Tag $tone={tone(e.level)}>{e.level}</Tag></span>
              <span className="logger" title={e.logger}>{e.logger}</span>
              <span className="msg" title={open === i ? undefined : e.msg}>
                {e.msg}
                {e.request_id && e.request_id !== '-' && (
                  <span className="rid" title={t('serverLog.byRequest')} onClick={(ev) => { ev.stopPropagation(); setF({ ...f, q: e.request_id! }) }}>[{e.request_id.slice(0, 8)}]</span>
                )}
              </span>
              {open === i && e.exc && <Exc>{e.exc}</Exc>}
            </Line>
          ))}
        </List>
      )}

      <Card style={{ marginTop: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{t('serverLog.levels')}</div>
        <Meta>{t('serverLog.levelsHint')}</Meta>
        {levels && (
          <LevelsGrid>
            {Object.entries(levels.levels).map(([name, level]) => (
              <div key={name} style={{ display: 'contents' }}>
                <span style={{ fontFamily: fonts.mono }}>{name}{levels.configured[name] ? ' ●' : ''}</span>
                <SearchSelect value={level} onChange={(v) => void changeLevel(name, v || 'NOTSET', true)} options={choiceOpts} />
              </div>
            ))}
            <SearchSelect sort allowCustom value={newLogger} onChange={setNewLogger} options={loggerOpts} placeholder={t('serverLog.pickLogger')} />
            <div style={{ display: 'flex', gap: 6 }}>
              <SearchSelect value={newLevel} onChange={(v) => setNewLevel(v || 'DEBUG')} options={choiceOpts} />
              <Button $size="sm" disabled={!newLogger} onClick={() => { void changeLevel(newLogger, newLevel, true); setNewLogger('') }}>{t('common.add')}</Button>
            </div>
          </LevelsGrid>
        )}
      </Card>
    </>
  )
}
