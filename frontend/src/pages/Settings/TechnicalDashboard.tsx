// Technical dashboard — superuser-only live view of the runtime: connected Socket.IO
// sessions, held record locks, per-pool SQLAlchemy stats, AI / license / runtime info,
// + a live log tail.
//
// Streams over the Socket.IO connection (see ../../sio/SioContext). On mount we
// subscribe to ``dashboard`` and ``logs`` rooms; the server pushes a full snapshot
// on every lock change + a 5s periodic refresh for pool stats. Unmount = unsubscribe.

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Activity, Database, Lock as LockIcon, Users, Cpu, KeyRound, Server, Pause, Play, Trash2 } from 'lucide-react'
import { Banner, Card, Tag, Mono } from '../../common'
import { useSio, useDashboardSnapshot, useLogStream } from '../../sio/SioContext'
import { useAuth } from '../../auth/AuthContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { LogEntry, PoolStats, HeldLock, ConnectedUser } from '../../sio/types'

// ── styled ──────────────────────────────────────────────────────────────────────────────

const CardGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 14px;
  margin-bottom: 14px;
`
const KpiRow = styled.div`
  display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 14px;
`
const Kpi = styled(Card)`
  flex: 1 1 180px;
  padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px;
  min-width: 180px;
`
const KpiLabel = styled.div`
  font-size: ${fontSize.micro};
  text-transform: uppercase; letter-spacing: 0.07em;
  color: ${colors.text.muted};
  display: inline-flex; align-items: center; gap: 6px;
`
const KpiValue = styled.div`
  font-size: ${fontSize['2xl']};
  font-family: ${fonts.sans}; font-weight: 600;
  color: ${colors.text.primary};
`
const KpiSub = styled.div` font-size: ${fontSize.sm}; color: ${colors.text.muted}; `
const SectionTitle = styled.div`
  font-size: ${fontSize.sm}; font-weight: 600;
  letter-spacing: 0.04em; text-transform: uppercase;
  color: ${colors.text.muted};
  display: inline-flex; align-items: center; gap: 6px;
  margin-bottom: 8px;
`
const ListTable = styled.table`
  width: 100%; border-collapse: collapse;
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  th, td {
    text-align: left; padding: 6px 10px;
    border-bottom: 1px solid ${colors.border};
    color: ${colors.text.secondary};
  }
  th {
    font-size: ${fontSize.micro}; text-transform: uppercase; letter-spacing: 0.06em;
    color: ${colors.text.muted}; font-weight: 600;
  }
  td.mono { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  tr:last-child td { border-bottom: none; }
`
const EmptyHint = styled.div`
  font-size: ${fontSize.sm}; color: ${colors.text.muted};
  font-style: italic; padding: 6px 0;
`
const LogPane = styled.div`
  background: ${colors.bg.base};
  border: 1px solid ${colors.border};
  border-radius: ${radius.md};
  padding: 10px;
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  height: 320px; overflow-y: auto;
  white-space: pre-wrap;
`
const LogLine = styled.div<{ $level: string }>`
  padding: 1px 0;
  color: ${({ $level }) => {
    switch ($level) {
      case 'ERROR': case 'CRITICAL': return colors.red.main
      case 'WARNING': return colors.orange.main
      case 'DEBUG': return colors.text.muted
      default: return colors.text.secondary
    }
  }};
  display: grid; grid-template-columns: 78px 60px 1fr; gap: 8px;
`
const LogTime = styled.span` color: ${colors.text.muted}; `
const LogLevel = styled.span` font-weight: 600; text-transform: uppercase; `
const LogControls = styled.div`
  display: flex; gap: 8px; align-items: center;
  margin-bottom: 8px; flex-wrap: wrap;
`
const LogFilter = styled.input`
  flex: 1; min-width: 120px; height: 26px; padding: 0 8px;
  background: ${colors.bg.input};
  border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.primary};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:focus { outline: none; border-color: ${colors.blue.border}; }
`
const PillBtn = styled.button<{ $active?: boolean }>`
  height: 26px; padding: 0 10px; border-radius: ${radius.sm};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  cursor: pointer; display: inline-flex; align-items: center; gap: 4px;
  &:hover { color: ${colors.text.primary}; }
`

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtUptime(s: number): string {
  if (s < 60) return `${Math.floor(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`
  const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60)
  return `${h}h ${m}m`
}

// ── sub-views ───────────────────────────────────────────────────────────────────────────

function PoolsCard({ pools }: { pools: PoolStats[] }) {
  const { t } = useTranslation()
  if (pools.length === 0) return <EmptyHint>{t('dashboard.tech.pools.empty', 'No pools configured.')}</EmptyHint>
  return (
    <ListTable>
      <thead>
        <tr>
          <th>{t('dashboard.tech.pools.name', 'Pool')}</th>
          <th>{t('dashboard.tech.pools.dialect', 'Dialect')}</th>
          <th style={{ textAlign: 'right' }}>{t('dashboard.tech.pools.checkedOut', 'In use')}</th>
          <th style={{ textAlign: 'right' }}>{t('dashboard.tech.pools.checkedIn', 'Idle')}</th>
          <th style={{ textAlign: 'right' }}>{t('dashboard.tech.pools.overflow', 'Overflow')}</th>
        </tr>
      </thead>
      <tbody>
        {pools.map((p) => (
          <tr key={p.name}>
            <td className="mono">{p.name}</td>
            <td><Tag $tone="blue">{p.dialect}</Tag></td>
            <td className="mono" style={{ textAlign: 'right' }}>
              {p.materialised ? (p.checked_out ?? 'n/a') : <Mono>{t('dashboard.tech.pools.lazy', 'not opened')}</Mono>}
            </td>
            <td className="mono" style={{ textAlign: 'right' }}>{p.materialised ? (p.checked_in ?? 'n/a') : ''}</td>
            <td className="mono" style={{ textAlign: 'right' }}>
              {p.materialised ? `${p.overflow ?? 0}${p.max_overflow != null ? `/${p.max_overflow}` : ''}` : ''}
            </td>
          </tr>
        ))}
      </tbody>
    </ListTable>
  )
}

function UsersCard({ users }: { users: ConnectedUser[] }) {
  const { t } = useTranslation()
  if (users.length === 0) return <EmptyHint>{t('dashboard.tech.users.empty', 'No connected sessions.')}</EmptyHint>
  return (
    <ListTable>
      <thead>
        <tr>
          <th>{t('dashboard.tech.users.user', 'User')}</th>
          <th>{t('dashboard.tech.users.session', 'Session')}</th>
          <th>{t('dashboard.tech.users.client', 'Client')}</th>
        </tr>
      </thead>
      <tbody>
        {users.map((u) => (
          <tr key={u.sid}>
            <td>{u.username}{u.is_superuser ? <Tag $tone="purple" style={{ marginLeft: 6 }}>super</Tag> : null}</td>
            <td className="mono">{u.sid.slice(0, 8)}…</td>
            <td className="mono">{u.client_id ? u.client_id.slice(0, 8) + '…' : '—'}</td>
          </tr>
        ))}
      </tbody>
    </ListTable>
  )
}

function LocksCard({ locks }: { locks: HeldLock[] }) {
  const { t } = useTranslation()
  if (locks.length === 0) return <EmptyHint>{t('dashboard.tech.locks.empty', 'No records currently locked.')}</EmptyHint>
  return (
    <ListTable>
      <thead>
        <tr>
          <th>{t('dashboard.tech.locks.who', 'User')}</th>
          <th>{t('dashboard.tech.locks.what', 'Record')}</th>
          <th>{t('dashboard.tech.locks.when', 'Since')}</th>
        </tr>
      </thead>
      <tbody>
        {locks.map((l, i) => {
          const kv = Object.entries(l.key_values).map(([k, v]) => `${k}=${v}`).join(', ')
          return (
            <tr key={i}>
              <td>{l.username}</td>
              <td className="mono">{l.app}.{l.screen} <Mono>· {kv}</Mono></td>
              <td>{fmtTime(l.acquired_at)}</td>
            </tr>
          )
        })}
      </tbody>
    </ListTable>
  )
}

function LogsCard() {
  const { t } = useTranslation()
  const { entries, denied } = useLogStream({ maxClientBuffer: 1000 })
  const [paused, setPaused] = useState(false)
  const [filter, setFilter] = useState('')
  const [levelFilter, setLevelFilter] = useState<'all' | 'info' | 'warn' | 'err'>('all')
  const paneRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo<LogEntry[]>(() => {
    const f = filter.trim().toLowerCase()
    return entries.filter((e) => {
      if (levelFilter === 'info' && !['INFO', 'WARNING', 'ERROR', 'CRITICAL'].includes(e.level)) return false
      if (levelFilter === 'warn' && !['WARNING', 'ERROR', 'CRITICAL'].includes(e.level)) return false
      if (levelFilter === 'err' && !['ERROR', 'CRITICAL'].includes(e.level)) return false
      if (f && !`${e.name} ${e.message}`.toLowerCase().includes(f)) return false
      return true
    })
  }, [entries, filter, levelFilter])

  useEffect(() => {
    if (paused || !paneRef.current) return
    paneRef.current.scrollTop = paneRef.current.scrollHeight
  }, [filtered, paused])

  if (denied) {
    return <Banner $tone="error">{t('dashboard.tech.logs.denied', 'Log viewer requires superuser access.')}</Banner>
  }

  return (
    <>
      <LogControls>
        <LogFilter
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t('dashboard.tech.logs.filterPlaceholder', 'Filter… (logger name or message)') ?? ''}
        />
        <PillBtn $active={levelFilter === 'all'} onClick={() => setLevelFilter('all')}>{t('dashboard.tech.logs.all', 'All')}</PillBtn>
        <PillBtn $active={levelFilter === 'info'} onClick={() => setLevelFilter('info')}>INFO+</PillBtn>
        <PillBtn $active={levelFilter === 'warn'} onClick={() => setLevelFilter('warn')}>WARN+</PillBtn>
        <PillBtn $active={levelFilter === 'err'} onClick={() => setLevelFilter('err')}>ERROR</PillBtn>
        <PillBtn $active={paused} onClick={() => setPaused((p) => !p)}>
          {paused ? <Play size={12} /> : <Pause size={12} />}
          {paused ? t('dashboard.tech.logs.resume', 'Resume') : t('dashboard.tech.logs.pause', 'Pause')}
        </PillBtn>
        <PillBtn onClick={() => setFilter('')}>
          <Trash2 size={12} /> {t('dashboard.tech.logs.clearFilter', 'Clear filter')}
        </PillBtn>
      </LogControls>
      <LogPane ref={paneRef}>
        {filtered.length === 0 ? (
          <EmptyHint>{t('dashboard.tech.logs.empty', 'No log lines match the current filter.')}</EmptyHint>
        ) : (
          filtered.map((e, i) => (
            <LogLine key={i} $level={e.level}>
              <LogTime>{fmtTime(e.ts)}</LogTime>
              <LogLevel>{e.level}</LogLevel>
              <span>
                <Mono>{e.name}</Mono> {e.message}
                {e.exc_info && (
                  <details style={{ marginTop: 4 }}>
                    <summary style={{ cursor: 'pointer', color: colors.text.muted }}>{t('dashboard.tech.logs.traceback', 'traceback')}</summary>
                    <pre style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', color: colors.red.main }}>{e.exc_info}</pre>
                  </details>
                )}
              </span>
            </LogLine>
          ))
        )}
      </LogPane>
    </>
  )
}

// ── main panel ──────────────────────────────────────────────────────────────────────────

export default function TechnicalDashboard(): ReactNode {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { status } = useSio()
  const snap = useDashboardSnapshot()

  if (!user?.is_superuser) {
    return <Banner $tone="error">{t('dashboard.tech.superuserOnly', 'Technical dashboard requires superuser access.')}</Banner>
  }
  if (snap === 'denied') {
    return <Banner $tone="error">{t('dashboard.tech.denied', 'Dashboard subscription denied by the server.')}</Banner>
  }
  if (!snap) {
    if (status === 'open') {
      return <Banner $tone="info">{t('dashboard.tech.waiting', 'Connected — waiting for first snapshot…')}</Banner>
    }
    if (status === 'closed') {
      return <Banner $tone="error">
        {t('dashboard.tech.disconnected',
          'Socket.IO disconnected — the page can\'t subscribe to live updates. Check the backend log; client will keep retrying.')}
      </Banner>
    }
    return <Banner $tone="info">{t('dashboard.tech.connecting', 'Connecting…')} ({status})</Banner>
  }

  const pools = snap.pools
  const users = snap.connected_users
  const locks = snap.locks
  const license = snap.license as { mode?: string; customer?: string; expires_at?: number | null }

  return (
    <div>
      <KpiRow>
        <Kpi>
          <KpiLabel><Users size={12} /> {t('dashboard.tech.kpi.users', 'Connected users')}</KpiLabel>
          <KpiValue>{users.length}</KpiValue>
          <KpiSub>{t('dashboard.tech.kpi.usersSub', 'Live Socket.IO sessions')}</KpiSub>
        </Kpi>
        <Kpi>
          <KpiLabel><LockIcon size={12} /> {t('dashboard.tech.kpi.locks', 'Active locks')}</KpiLabel>
          <KpiValue>{locks.length}</KpiValue>
          <KpiSub>{t('dashboard.tech.kpi.locksSub', 'Records currently being edited')}</KpiSub>
        </Kpi>
        <Kpi>
          <KpiLabel><Database size={12} /> {t('dashboard.tech.kpi.pools', 'Pools')}</KpiLabel>
          <KpiValue>{pools.filter((p) => p.materialised).length} / {pools.length}</KpiValue>
          <KpiSub>{t('dashboard.tech.kpi.poolsSub', 'Materialised / configured')}</KpiSub>
        </Kpi>
        <Kpi>
          <KpiLabel><Activity size={12} /> {t('dashboard.tech.kpi.uptime', 'Uptime')}</KpiLabel>
          <KpiValue>{fmtUptime(snap.uptime_s)}</KpiValue>
          <KpiSub>Python {snap.runtime.python}</KpiSub>
        </Kpi>
      </KpiRow>

      <CardGrid>
        <Card style={{ padding: 14 }}>
          <SectionTitle><Database size={12} /> {t('dashboard.tech.pools.heading', 'Database pools')}</SectionTitle>
          <PoolsCard pools={pools} />
        </Card>
        <Card style={{ padding: 14 }}>
          <SectionTitle><Users size={12} /> {t('dashboard.tech.users.heading', 'Connected users')}</SectionTitle>
          <UsersCard users={users} />
        </Card>
        <Card style={{ padding: 14 }}>
          <SectionTitle><LockIcon size={12} /> {t('dashboard.tech.locks.heading', 'Record locks')}</SectionTitle>
          <LocksCard locks={locks} />
        </Card>
        <Card style={{ padding: 14 }}>
          <SectionTitle><Cpu size={12} /> {t('dashboard.tech.runtime.heading', 'Runtime')}</SectionTitle>
          <ListTable>
            <tbody>
              <tr><td>Connectors</td><td className="mono">{snap.runtime.connector_count}</td></tr>
              <tr><td>Screens</td><td className="mono">{snap.runtime.screen_count}</td></tr>
              <tr><td>Platform</td><td className="mono"><Mono>{snap.runtime.platform}</Mono></td></tr>
              <tr><td>AI</td><td>{snap.ai.enabled ? <><Tag $tone="green">on</Tag> <Mono>{snap.ai.model ?? ''}</Mono></> : <Tag>off</Tag>}</td></tr>
              <tr>
                <td><KeyRound size={11} style={{ verticalAlign: 'middle' }} /> License</td>
                <td>
                  {license.mode === 'full' ? <Tag $tone="green">full</Tag> : <Tag $tone="orange">restricted</Tag>}
                  {license.customer ? <> <Mono>{license.customer}</Mono></> : null}
                </td>
              </tr>
              <tr>
                <td><Server size={11} style={{ verticalAlign: 'middle' }} /> {t('dashboard.tech.runtime.sio', 'Socket.IO')}</td>
                <td><Tag $tone={status === 'open' ? 'green' : status === 'connecting' ? 'orange' : 'red'}>{status}</Tag></td>
              </tr>
            </tbody>
          </ListTable>
        </Card>
      </CardGrid>

      <Card style={{ padding: 14 }}>
        <SectionTitle><Activity size={12} /> {t('dashboard.tech.logs.heading', 'Live log tail')}</SectionTitle>
        <LogsCard />
      </Card>
    </div>
  )
}
