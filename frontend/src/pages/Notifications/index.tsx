// The inbox: every notification delivered to the current user, newest first, with an unread
// filter. A click marks the row read and opens its link when it has one. Opens as a page tab
// (/notifications) from the bell's "View all".
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Bell, CheckCheck, RefreshCw } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Centered, PageLayout } from '../../common'
import { useSio } from '../../sio/SioContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { Notification } from '../../types/notifications'
import { relative } from '../Nomaflow/util'

const Toolbar = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;`
const Tabs = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden; background: ${colors.bg.input};`
const Tab = styled.button<{ $active: boolean }>`
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')}; border: none; cursor: pointer;
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; padding: 6px 14px;
  &:hover { color: ${colors.text.primary}; }
`
const Card = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; overflow: hidden; display: flex; flex-direction: column; flex: 1; min-height: 0;`
const ListBody = styled.div`flex: 1; min-height: 0; overflow-y: auto;`
const Row = styled.button<{ $unread: boolean }>`
  display: flex; align-items: flex-start; gap: 12px; width: 100%; text-align: left; padding: 12px 16px 12px ${({ $unread }) => ($unread ? '13px' : '16px')};
  border: none; border-bottom: 1px solid ${colors.border}; border-left: 3px solid ${({ $unread }) => ($unread ? colors.blue.main : 'transparent')};
  background: ${({ $unread }) => ($unread ? colors.blue.bg : 'transparent')}; cursor: pointer;
  &:hover { background: var(--hover-subtle); }
`
const Body = styled.div`flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px;`
const TopLine = styled.div`display: flex; align-items: center; gap: 10px; min-width: 0;`
const SubjectLine = styled.div<{ $unread: boolean }>`
  flex: 1; min-width: 0; font-weight: ${({ $unread }) => ($unread ? 600 : 500)}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: ${fonts.sans};
`
const When = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; flex-shrink: 0;`
const Rule = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; flex-shrink: 0;`
const MessageLine = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.secondary}; line-height: 1.4; white-space: pre-wrap; font-family: ${fonts.sans};`
const Empty = styled.div`padding: 40px; text-align: center; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`

export default function NotificationsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { subscribeNotifications } = useSio()
  const [filter, setFilter] = useState<'all' | 'unread'>('all')
  const [items, setItems] = useState<Notification[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      setItems((await api.get<{ notifications: Notification[] }>(`/api/notifications?limit=200${filter === 'unread' ? '&unread=1' : ''}`)).notifications)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
  }, [filter])
  useEffect(() => { void load() }, [load])
  useEffect(() => subscribeNotifications((n) => setItems((cur) => (cur ? [n, ...cur] : cur))), [subscribeNotifications])

  const markAll = async () => {
    try { await api.post('/api/notifications/read', {}) } catch { /* ignore */ }
    await load()
  }
  const openOne = async (n: Notification) => {
    if (!n.read) {
      try { await api.post('/api/notifications/read', { ids: [n.id] }) } catch { /* ignore */ }
      setItems((cur) => cur?.map((x) => (x.id === n.id ? { ...x, read: true } : x)) ?? cur)
    }
    if (n.link) navigate(n.link)
  }
  const unread = items?.filter((n) => !n.read).length ?? 0

  return (
    <PageLayout icon={<Bell size={18} />} title={t('notifications.title')} description={<span>{t('notifications.subtitle')}</span>}>
      <Toolbar>
        <Tabs>
          <Tab $active={filter === 'all'} onClick={() => setFilter('all')}>{t('notifications.all')}</Tab>
          <Tab $active={filter === 'unread'} onClick={() => setFilter('unread')}>{t('notifications.unread')}{unread > 0 ? ` (${unread})` : ''}</Tab>
        </Tabs>
        <div style={{ flex: 1 }} />
        <Button $size="sm" $variant="ghost" onClick={() => void load()} title={t('common.refresh', 'Refresh')}><RefreshCw size={13} /></Button>
        <Button $size="sm" $variant="ghost" onClick={() => void markAll()} disabled={unread === 0}><CheckCheck size={13} /> {t('notifications.markAllRead')}</Button>
      </Toolbar>
      {error && <Banner $tone="error">{error}</Banner>}
      <Card>
        <ListBody>
          {items === null ? <Centered />
            : items.length === 0 ? <Empty>{t('notifications.empty')}</Empty>
            : items.map((n) => (
              <Row key={n.id} type="button" $unread={!n.read} onClick={() => void openOne(n)}>
                <Body>
                  <TopLine>
                    <SubjectLine $unread={!n.read}>{n.subject}</SubjectLine>
                    {n.rule && <Rule>{n.rule}</Rule>}
                    <When>{relative(n.created_at)}</When>
                  </TopLine>
                  {n.body && <MessageLine>{n.body}</MessageLine>}
                </Body>
              </Row>
            ))}
        </ListBody>
      </Card>
    </PageLayout>
  )
}
