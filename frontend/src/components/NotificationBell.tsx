// The bell in the header: an unread badge, a dropdown with the latest notifications, "mark all
// read" and "view all" (the inbox page). The count comes from /api/notifications/unread_count,
// refreshed when the server pushes a `notification` event to this user's Socket.IO room and on a
// slow poll as a fallback. Clicking a row marks it read and opens its link when it has one.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Bell } from 'lucide-react'
import { api } from '../api/client'
import { PortalPanel } from '../common'
import { useSio } from '../sio/SioContext'
import { colors, fonts, fontSize, radius } from '../theme'
import type { Notification } from '../types/notifications'
import { relative } from '../pages/Nomaflow/util'

const POLL_MS = 60_000
const PREVIEW_LIMIT = 6

const Wrap = styled.div`position: relative; display: inline-flex;`
const Badge = styled.span`
  position: absolute; top: -3px; right: -3px; min-width: 16px; height: 16px; padding: 0 4px;
  border-radius: 8px; background: ${colors.red.main}; color: ${colors.text.inverse};
  font-size: ${fontSize.micro}; font-weight: 700; font-family: ${fonts.sans};
  display: flex; align-items: center; justify-content: center; line-height: 1; pointer-events: none;
`
const Panel = styled.div`width: 360px; max-height: 480px; display: flex; flex-direction: column; min-height: 0;`
const PanelHeader = styled.div`
  display: flex; align-items: center; justify-content: space-between; padding: 10px 12px;
  border-bottom: 1px solid ${colors.border}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary};
`
const HeaderBtn = styled.button`
  background: none; border: none; color: ${colors.text.muted}; font-size: ${fontSize.micro}; cursor: pointer;
  padding: 2px 6px; border-radius: ${radius.sm}; font-family: ${fonts.sans};
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`
const ListBody = styled.div`flex: 1; min-height: 0; overflow-y: auto;`
const Row = styled.button<{ $unread: boolean }>`
  display: flex; flex-direction: column; gap: 2px; width: 100%; text-align: left; padding: 10px 12px;
  border: none; border-bottom: 1px solid ${colors.border}; cursor: pointer;
  background: ${({ $unread }) => ($unread ? colors.blue.bg : 'transparent')};
  &:hover { background: var(--hover-subtle); }
`
const RowTop = styled.div`display: flex; align-items: center; gap: 6px; font-size: ${fontSize.sm}; color: ${colors.text.primary}; min-width: 0;`
const Dot = styled.span<{ $on: boolean }>`width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; background: ${({ $on }) => ($on ? colors.blue.main : 'transparent')};`
const Subject = styled.span`flex: 1; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: ${fonts.sans};`
const When = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; flex-shrink: 0;`
const Body = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: ${fonts.sans};`
const Empty = styled.div`padding: 24px; text-align: center; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`
const PanelFooter = styled.div`padding: 8px 12px; border-top: 1px solid ${colors.border}; text-align: center;`
const ViewAll = styled.button`background: none; border: none; color: ${colors.blue.main}; font-size: ${fontSize.sm}; cursor: pointer; font-family: ${fonts.sans}; &:hover { text-decoration: underline; }`

export function NotificationBell({ className, children }: { className?: string; children: (open: boolean) => React.ReactNode }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { subscribeNotifications } = useSio()
  const [unread, setUnread] = useState(0)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Notification[] | null>(null)
  const triggerRef = useRef<HTMLDivElement | null>(null)

  const refreshCount = useCallback(async () => {
    try { setUnread((await api.get<{ unread: number }>('/api/notifications/unread_count')).unread) } catch { /* the bell is best-effort */ }
  }, [])
  const loadPreview = useCallback(async () => {
    try { setItems((await api.get<{ notifications: Notification[] }>(`/api/notifications?limit=${PREVIEW_LIMIT}`)).notifications) } catch { setItems([]) }
  }, [])

  useEffect(() => {
    void refreshCount()
    const h = window.setInterval(() => { void refreshCount() }, POLL_MS)
    return () => window.clearInterval(h)
  }, [refreshCount])
  useEffect(() => subscribeNotifications((n) => {
    setUnread((c) => c + 1)
    setItems((cur) => (cur ? [n, ...cur].slice(0, PREVIEW_LIMIT) : cur))
  }), [subscribeNotifications])

  const toggle = () => { const next = !open; setOpen(next); if (next) void loadPreview() }
  const markAll = async () => {
    try { await api.post('/api/notifications/read', {}) } catch { /* ignore */ }
    setUnread(0); setItems((cur) => cur?.map((n) => ({ ...n, read: true })) ?? cur)
  }
  const openOne = async (n: Notification) => {
    if (!n.read) {
      try { await api.post('/api/notifications/read', { ids: [n.id] }) } catch { /* ignore */ }
      setUnread((c) => Math.max(0, c - 1)); setItems((cur) => cur?.map((x) => (x.id === n.id ? { ...x, read: true } : x)) ?? cur)
    }
    setOpen(false)
    if (n.link) navigate(n.link)
  }

  return (
    <Wrap ref={triggerRef} className={className}>
      <span onClick={toggle} style={{ display: 'inline-flex', position: 'relative' }}>
        {children(open)}
        {unread > 0 && <Badge>{unread > 99 ? '99+' : unread}</Badge>}
      </span>
      {open && (
        <PortalPanel triggerRef={triggerRef} maxWidth={360} minWidth={360} roomBelow={400} onDismiss={() => setOpen(false)}>
          <Panel>
            <PanelHeader>
              <span>{t('notifications.title')}</span>
              {unread > 0 && <HeaderBtn type="button" onClick={() => void markAll()}>{t('notifications.markAllRead')}</HeaderBtn>}
            </PanelHeader>
            <ListBody>
              {items === null ? <Empty>{t('common.loading')}</Empty>
                : items.length === 0 ? <Empty>{t('notifications.empty')}</Empty>
                : items.map((n) => (
                  <Row key={n.id} type="button" $unread={!n.read} onClick={() => void openOne(n)}>
                    <RowTop><Dot $on={!n.read} /><Subject>{n.subject}</Subject><When>{relative(n.created_at)}</When></RowTop>
                    {n.body && <Body>{n.body}</Body>}
                  </Row>
                ))}
            </ListBody>
            <PanelFooter>
              <ViewAll type="button" onClick={() => { setOpen(false); navigate('/notifications') }}>{t('notifications.viewAll')}</ViewAll>
            </PanelFooter>
          </Panel>
        </PortalPanel>
      )}
    </Wrap>
  )
}

export { Bell as BellIcon }
