// Settings → Notifications: the rules of notifications.toml. A list on the left, the rule editor on
// the right: a trigger (a screen event with its app / screen / events / row condition, a job event
// with its jobs and threshold, or "fired by a screen action"), the channels, the recipients, the
// subject / body / link templates, and the action channel's target. "Send a test" dispatches the
// rule with sample values through POST /admin/notify/test so SMTP and webhooks can be checked.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Bell, Plus, Send, Trash2, X } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, Checkbox, Field, Input, SearchSelect, SpinnerRing, Tag, Textarea, useModals, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { validateId } from '../../services/idValidator'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { DispatchReportWire, JobEvent, NotificationChannel, NotificationRule, RuleTrigger, RuleWhen, ScreenEvent } from '../../types/notifications'
import { PermChip } from './Access/OtherAccess'

const Split = styled.div`display: flex; gap: 12px; flex: 1; min-height: 0;`
const Side = styled.div`width: 300px; flex-shrink: 0; display: flex; flex-direction: column; gap: 10px; min-height: 0;`
const List = styled(Card)`flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px;`
const Item = styled.div<{ $selected?: boolean }>`
  display: flex; align-items: center; gap: 10px; padding: 9px 11px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${({ $selected }) => ($selected ? colors.blue.border : colors.border)};
  background: ${({ $selected }) => ($selected ? colors.blue.bg : colors.bg.input)}; min-width: 0;
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; }
  & .sub { font-size: ${fontSize.micro}; color: ${colors.text.muted}; display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }
`
const Panel = styled.div`flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: ${colors.bg.card}; overflow: hidden;`
const Head = styled.div`display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-bottom: 1px solid ${colors.border}; & .title { font-size: ${fontSize.lg}; font-weight: 700; color: ${colors.text.primary}; font-family: ${fonts.mono}; } & .dirty { font-size: ${fontSize.micro}; color: ${colors.orange.main}; font-weight: 600; }`
const Body = styled.div`flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 14px;`
const Foot = styled.div`display: flex; gap: 8px; padding: 10px 16px; border-top: 1px solid ${colors.border}; align-items: center;`
const Section = styled.div`& > .h { font-size: ${fontSize.sm}; font-weight: 700; color: ${colors.text.primary}; margin-bottom: 6px; } & > .h .hint { font-weight: 500; color: ${colors.text.muted}; margin-left: 6px; }`
const Grid = styled.div`display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px;`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean }>`
  padding: 5px 10px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')}; color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 5px; align-items: center;`
const Placeholder = styled(Card)`flex: 1; display: flex; align-items: center; justify-content: center; color: ${colors.text.muted}; font-size: ${fontSize.sm};`

const SCREEN_EVENTS: ScreenEvent[] = ['insert', 'update', 'delete']
const JOB_EVENTS: JobEvent[] = ['failure', 'succeeded', 'canceled', 'missed', 'long_run']
const CHANNELS: NotificationChannel[] = ['portal', 'email', 'action']

type Doc = Record<string, NotificationRule>

type Tr = (key: string, defaultValue?: string) => string

function triggerSummary(r: NotificationRule, t: Tr): string {
  const tr = r.trigger
  if (tr.kind === 'screen') return `${tr.app}.${tr.screen} · ${(tr.events ?? SCREEN_EVENTS).join(', ')}${tr.when?.length ? ' · when' : ''}`
  if (tr.kind === 'job') return `${t('settings.notify.job', 'job')} ${(tr.jobs ?? []).join(', ') || '*'} · ${(tr.events ?? ['failure']).join(', ')}`
  return t('settings.notify.byAction', 'fired by a screen action')
}

function RuleEditor({ id, initial, isNew, takenIds, jobIds, onSaved, onCancel, onDelete }: {
  id: string; initial: NotificationRule; isNew: boolean; takenIds: string[]; jobIds: string[]
  onSaved: (id: string, rule: NotificationRule) => Promise<void>; onCancel: () => void; onDelete?: () => void
}) {
  const { t } = useTranslation()
  const { connectors, screens } = useWorkspace()
  const [rid, setRid] = useState(id)
  const [rule, setRule] = useState<NotificationRule>(() => JSON.parse(JSON.stringify(initial)))
  const [recipientKind, setRecipientKind] = useState<'user' | 'role' | 'email'>('role')
  const [recipientText, setRecipientText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testReport, setTestReport] = useState<DispatchReportWire | null>(null)
  const [users, setUsers] = useState<string[]>([])
  const [roles, setRoles] = useState<string[]>([])
  useEffect(() => {
    api.get<{ users: { username: string }[] }>('/admin/access/users').then((r) => setUsers(r.users.map((u) => u.username))).catch(() => undefined)
    api.get<{ roles: { name: string }[] }>('/admin/access/roles').then((r) => setRoles(r.roles.map((x) => x.name))).catch(() => undefined)
  }, [])

  const dirty = isNew || JSON.stringify(rule) !== JSON.stringify(initial)
  const patch = (p: Partial<NotificationRule>) => setRule((r) => ({ ...r, ...p }))
  const setTrigger = (tr: RuleTrigger) => patch({ trigger: tr })
  const tr = rule.trigger
  const apps = Object.keys(screens ?? {})
  const appOpts: SearchSelectOption[] = apps.map((a) => ({ value: a, label: a, mono: a }))
  const screenOpts: SearchSelectOption[] = tr.kind === 'screen' ? (screens?.[tr.app] ?? []).map((s) => ({ value: s.id, label: s.label || s.id, mono: s.id })) : []
  const jobOpts: SearchSelectOption[] = jobIds.map((j) => ({ value: j, label: j, mono: j }))
  const sqlConns = (connectors ?? []).filter((c) => c.type === 'sql')
  const connOpts: SearchSelectOption[] = (connectors ?? []).map((c) => ({ value: c.name, label: `${c.name} (${c.type})`, mono: c.name }))
  const recipientOpts: SearchSelectOption[] = recipientKind === 'user' ? users.map((u) => ({ value: u, label: u, mono: u })) : recipientKind === 'role' ? roles.map((r) => ({ value: r, label: r, mono: r })) : []
  const channels = rule.channels ?? ['portal']
  const toggleIn = <T,>(list: T[] | undefined, v: T, fallback: T[]): T[] => { const cur = list ?? fallback; return cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] }
  const addRecipient = (v: string) => { const s = v.trim(); if (!s) return; const entry = `${recipientKind}:${s}`; if (!(rule.recipients ?? []).includes(entry)) patch({ recipients: [...(rule.recipients ?? []), entry] }); setRecipientText('') }

  const save = async () => {
    const check = validateId({ kind: 'actions', proposed: rid, existing: takenIds, mode: isNew ? 'add' : 'rename', currentName: id })
    if (check.error) { setError(check.error); return }
    if (!rule.subject.trim()) { setError(t('settings.notify.errSubject', 'A subject is required.')); return }
    if (tr.kind === 'screen' && (!tr.app || !tr.screen)) { setError(t('settings.notify.errScreen', 'Pick the app and the screen.')); return }
    setBusy(true); setError(null)
    try { await onSaved(rid.trim(), rule) } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }
  const sendTest = async () => {
    setBusy(true); setError(null); setTestReport(null)
    try {
      const sample: Record<string, unknown> = { USER: 'test', APP: tr.kind === 'screen' ? tr.app : '', SCREEN: tr.kind === 'screen' ? tr.screen : '', EVENT: 'test', JOB: 'test-job', STATE: 'FAILED', ERROR: 'sample error', RUN_ID: 'run-test' }
      const action = rule.action?.connector ? { connector: rule.action.connector, query: rule.action.query || undefined, endpoint: rule.action.endpoint || undefined, params: rule.action.params ?? {} } : undefined
      setTestReport(await api.post<DispatchReportWire>('/admin/notify/test', { channels, recipients: rule.recipients ?? [], subject: rule.subject, body: rule.body ?? null, link: rule.link ?? null, payload: sample, action }))
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <Panel>
      <Head><Bell size={16} color={colors.blue.main} /><span className="title">{isNew ? t('settings.notify.newRule', 'New rule') : `[notifications.${id}]`}</span>{dirty && !isNew && <span className="dirty">{t('settings.access.unsaved', 'unsaved changes')}</span>}</Head>
      <Body>
        {error && <Banner $tone="error">{error}</Banner>}
        {testReport && (
          <Banner $tone={Object.keys(testReport.errors).length ? 'error' : 'ok'}>
            {t('settings.notify.testResult', 'Test sent — portal: {{portal}} · email: {{email}} · action: {{action}}', { portal: testReport.portal_users.join(', ') || '—', email: testReport.emails.join(', ') || '—', action: testReport.action_ran ? 'ok' : '—' })}
            {Object.entries(testReport.errors).map(([k, v]) => <div key={k}>{k}: {v}</div>)}
          </Banner>
        )}
        <Grid>
          <Field label={t('settings.notify.id', 'Rule id')}><Input value={rid} disabled={!isNew} onChange={(e) => setRid(e.target.value)} placeholder="period_closed" /></Field>
          <Field label={t('settings.notify.label', 'Label')}><Input value={rule.label ?? ''} onChange={(e) => patch({ label: e.target.value || null })} /></Field>
          <div style={{ display: 'flex', alignItems: 'end', paddingBottom: 6 }}><Checkbox checked={rule.enabled !== false} onChange={(v: boolean) => patch({ enabled: v })} label={t('settings.notify.enabled', 'Enabled')} /></div>
        </Grid>

        <Section>
          <div className="h">{t('settings.notify.trigger', 'Trigger')}</div>
          <Seg style={{ marginBottom: 10 }}>
            <SegBtn $active={tr.kind === 'screen'} onClick={() => setTrigger({ kind: 'screen', app: apps[0] ?? '', screen: '', events: ['update'], when: [] })}>{t('settings.notify.kindScreen', 'A screen event')}</SegBtn>
            <SegBtn $active={tr.kind === 'job'} onClick={() => setTrigger({ kind: 'job', events: ['failure'], jobs: [] })}>{t('settings.notify.kindJob', 'A job event')}</SegBtn>
            <SegBtn $active={tr.kind === 'action'} onClick={() => setTrigger({ kind: 'action' })}>{t('settings.notify.kindAction', 'A screen action')}</SegBtn>
          </Seg>
          {tr.kind === 'screen' && (
            <>
              <Grid>
                <Field label={t('settings.notify.app', 'App')}><SearchSelect value={tr.app} onChange={(v) => setTrigger({ ...tr, app: v, screen: '' })} options={appOpts} /></Field>
                <Field label={t('settings.notify.screen', 'Screen')}><SearchSelect value={tr.screen} onChange={(v) => setTrigger({ ...tr, screen: v })} options={screenOpts} allowCustom /></Field>
                <Field label={t('settings.notify.events', 'Events')}>
                  <div style={{ display: 'flex', gap: 12, paddingTop: 8 }}>
                    {SCREEN_EVENTS.map((ev) => <Checkbox key={ev} checked={(tr.events ?? SCREEN_EVENTS).includes(ev)} onChange={() => setTrigger({ ...tr, events: toggleIn(tr.events, ev, SCREEN_EVENTS) })} label={ev} />)}
                  </div>
                </Field>
              </Grid>
              <Field label={t('settings.notify.when', 'Only when the written row has (AND)')}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {(tr.when ?? []).map((w: RuleWhen, i: number) => (
                    <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: 8 }}>
                      <Input value={w.field} placeholder="STATUS" onChange={(e) => setTrigger({ ...tr, when: (tr.when ?? []).map((x, j) => (j === i ? { ...x, field: e.target.value.toUpperCase() } : x)) })} />
                      <Input value={Array.isArray(w.value) ? w.value.join(', ') : w.value} placeholder="closed, validated" onChange={(e) => { const parts = e.target.value.split(',').map((s) => s.trim()).filter(Boolean); setTrigger({ ...tr, when: (tr.when ?? []).map((x, j) => (j === i ? { ...x, value: parts.length > 1 ? parts : (parts[0] ?? '') } : x)) }) }} />
                      <Button $size="sm" $variant="ghost" onClick={() => setTrigger({ ...tr, when: (tr.when ?? []).filter((_, j) => j !== i) })}><X size={13} /></Button>
                    </div>
                  ))}
                  <div><Button $size="sm" $variant="ghost" onClick={() => setTrigger({ ...tr, when: [...(tr.when ?? []), { field: '', value: '' }] })}><Plus size={13} /> {t('settings.notify.addWhen', 'Add a condition')}</Button></div>
                </div>
              </Field>
            </>
          )}
          {tr.kind === 'job' && (
            <Grid>
              <Field label={t('settings.notify.jobs', 'Jobs (empty = every job)')}>
                <Chips>
                  {(tr.jobs ?? []).map((j) => <PermChip key={j}>{j}<button type="button" onClick={() => setTrigger({ ...tr, jobs: (tr.jobs ?? []).filter((x) => x !== j) })}><X size={11} /></button></PermChip>)}
                  <div style={{ minWidth: 160 }}><SearchSelect value="" onChange={(v) => { if (v && !(tr.jobs ?? []).includes(v)) setTrigger({ ...tr, jobs: [...(tr.jobs ?? []), v] }) }} options={jobOpts.filter((o) => !(tr.jobs ?? []).includes(o.value))} allowCustom placeholder={t('settings.notify.addJob', 'Add a job…')} /></div>
                </Chips>
              </Field>
              <Field label={t('settings.notify.events', 'Events')}>
                <div style={{ display: 'flex', gap: 12, paddingTop: 8, flexWrap: 'wrap' }}>
                  {JOB_EVENTS.map((ev) => <Checkbox key={ev} checked={(tr.events ?? ['failure']).includes(ev)} onChange={() => setTrigger({ ...tr, events: toggleIn(tr.events, ev, ['failure']) })} label={ev} />)}
                </div>
              </Field>
              <Field label={t('settings.notify.longRun', 'Long run threshold (minutes)')}>
                <Input type="number" min={1} value={tr.long_run_minutes ?? ''} onChange={(e) => setTrigger({ ...tr, long_run_minutes: e.target.value ? Number(e.target.value) : null })} disabled={!(tr.events ?? []).includes('long_run')} />
              </Field>
            </Grid>
          )}
        </Section>

        <Section>
          <div className="h">{t('settings.notify.delivery', 'Delivery')}</div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 10 }}>
            {CHANNELS.map((c) => <Checkbox key={c} checked={channels.includes(c)} onChange={() => patch({ channels: toggleIn(rule.channels, c, ['portal']) })} label={t(`settings.notify.channel_${c}`, c)} />)}
          </div>
          <Field label={t('settings.notify.recipients', 'Recipients')}>
            <Chips>
              {(rule.recipients ?? []).map((r) => <PermChip key={r}>{r}<button type="button" onClick={() => patch({ recipients: (rule.recipients ?? []).filter((x) => x !== r) })}><X size={11} /></button></PermChip>)}
              <Seg>
                {(['role', 'user', 'email'] as const).map((k) => <SegBtn key={k} $active={recipientKind === k} onClick={() => { setRecipientKind(k); setRecipientText('') }}>{t(`settings.notify.rk_${k}`, k)}</SegBtn>)}
              </Seg>
              <div style={{ minWidth: 220, flex: 1 }}>
                <SearchSelect value={recipientText} onChange={addRecipient} options={recipientOpts} allowCustom placeholder={recipientKind === 'email' ? 'name@example.com' : t('settings.notify.pickRecipient', 'Pick or type…')} />
              </div>
            </Chips>
          </Field>
          {channels.includes('action') && (
            <Grid style={{ marginTop: 10 }}>
              <Field label={t('settings.notify.actionConnector', 'Action: connector')}><SearchSelect value={rule.action?.connector ?? ''} onChange={(v) => patch({ action: { ...(rule.action ?? { connector: '' }), connector: v } })} options={connOpts} /></Field>
              <Field label={t('settings.notify.actionQuery', 'Query (SQL connector)')}><Input value={rule.action?.query ?? ''} onChange={(e) => patch({ action: { ...(rule.action ?? { connector: '' }), query: e.target.value || null } })} disabled={!sqlConns.some((c) => c.name === rule.action?.connector)} /></Field>
              <Field label={t('settings.notify.actionEndpoint', 'Endpoint (API connector)')}><Input value={rule.action?.endpoint ?? ''} onChange={(e) => patch({ action: { ...(rule.action ?? { connector: '' }), endpoint: e.target.value || null } })} /></Field>
            </Grid>
          )}
        </Section>

        <Section>
          <div className="h">{t('settings.notify.message', 'Message')}</div>
          <Field label={t('settings.notify.subject', 'Subject')}><Input value={rule.subject} onChange={(e) => patch({ subject: e.target.value })} placeholder="Period {PERIOD} closed by {USER}" /></Field>
          <Field label={t('settings.notify.body', 'Body')}><Textarea value={rule.body ?? ''} onChange={(e) => patch({ body: e.target.value || null })} rows={3} /></Field>
          <Field label={t('settings.notify.link', 'Link (a route the notification opens)')}><Input value={rule.link ?? ''} onChange={(e) => patch({ link: e.target.value || null })} placeholder="/screen/nomajde/period_status" /></Field>
        </Section>
      </Body>
      <Foot>
        {onDelete && <Button $size="sm" $variant="ghost" onClick={onDelete}><Trash2 size={13} /> {t('common.delete')}</Button>}
        <Button $size="sm" $variant="ghost" onClick={() => void sendTest()} disabled={busy} title={t('settings.notify.sendTestTitle', 'Dispatch this rule with sample values to its recipients')}><Send size={13} /> {t('settings.notify.sendTest', 'Send a test')}</Button>
        <div style={{ flex: 1 }} />
        <Button $size="sm" $variant="ghost" onClick={onCancel}>{t('common.cancel', 'Cancel')}</Button>
        <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={busy || !dirty}>{busy ? <SpinnerRing size={13} thickness={2} /> : t('common.save')}</Button>
      </Foot>
    </Panel>
  )
}

export default function NotificationsBuilder() {
  const { t } = useTranslation()
  const { confirm } = useModals()
  const [doc, setDoc] = useState<Doc | null>(null)
  const [jobIds, setJobIds] = useState<string[]>([])
  const [editing, setEditing] = useState<string | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.get<{ notifications: Doc }>('/admin/config/notifications/parsed').then((r) => setDoc(r.notifications)).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
    api.get<{ jobs: { id: string }[] }>('/admin/jobs').then((r) => setJobIds(r.jobs.map((j) => j.id))).catch(() => undefined)
  }, [])
  useEffect(load, [load])

  const persist = async (next: Doc) => {
    await api.put('/admin/config/notifications/parsed', { notifications: next })
    setDoc(next)
  }
  const onSaved = async (id: string, rule: NotificationRule) => { await persist({ ...(doc ?? {}), [id]: rule }); setEditing(id) }
  const remove = async (id: string) => {
    if (!(await confirm({ title: t('common.delete'), message: t('settings.notify.confirmDelete', 'Delete the rule {{id}}?', { id }), variant: 'danger' }))) return
    const next = { ...(doc ?? {}) }; delete next[id]
    try { await persist(next); setEditing(undefined) } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
  }
  const ids = useMemo(() => Object.keys(doc ?? {}), [doc])

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Centered />
  const blank: NotificationRule = { enabled: true, trigger: { kind: 'screen', app: '', screen: '', events: ['update'], when: [] }, channels: ['portal'], recipients: [], subject: '' }

  return (
    <Split>
      <Side>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}><Button $size="sm" $variant="primary" onClick={() => setEditing(null)}><Plus size={13} /> {t('settings.notify.addRule', 'Add rule')}</Button></div>
        {error && <Banner $tone="error">{error}</Banner>}
        <List>
          {ids.length === 0 && <span style={{ fontSize: fontSize.sm, color: colors.text.muted, padding: 8 }}>{t('settings.notify.none', 'No rule yet.')}</span>}
          {ids.map((id) => {
            const r = doc[id]
            return (
              <Item key={id} $selected={editing === id} onClick={() => setEditing(id)}>
                <Bell size={15} color={colors.text.muted} />
                <span className="text">
                  <span className="name">{id}</span>
                  <span className="sub">
                    {r.enabled === false && <Tag $tone="red">{t('settings.notify.disabled', 'disabled')}</Tag>}
                    <span>{triggerSummary(r, (k, d) => t(k, d ?? k))}</span>
                    {(r.channels ?? ['portal']).map((c) => <Tag key={c} $tone="blue">{c}</Tag>)}
                  </span>
                </span>
              </Item>
            )
          })}
        </List>
      </Side>
      {editing !== undefined ? (
        <RuleEditor key={editing ?? '__new__'} id={editing ?? ''} initial={editing ? doc[editing] : blank} isNew={editing == null} takenIds={ids} jobIds={jobIds}
          onSaved={onSaved} onCancel={() => setEditing(undefined)} onDelete={editing ? () => void remove(editing) : undefined} />
      ) : (
        <Placeholder>{t('settings.notify.pick', 'Pick a rule to edit, or add one.')}</Placeholder>
      )}
    </Split>
  )
}
