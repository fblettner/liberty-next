// Package → Promotion (C11): the target instances this one promotes to (saved in app.toml, the
// password encrypted; a Test signs in there) and the history of the promotions applied from here —
// a row opens on the target's report.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { ChevronDown, ChevronRight, Plug, Plus, Save, Trash2 } from 'lucide-react'
import { Banner, Button, Card, Checkbox, Input, PasswordInput, SpinnerRing, Tag } from '../../common'
import { colors, fontSize, fonts } from '../../theme'
import { ApiError } from '../../api/client'
import { getHistory, getHistoryEntry, getTargets, isPlan, saveTargets, testTarget } from '../../services/promotion'
import type { HistoryEntry, PromotionTarget } from '../../types/promotion'
import { PackagePlanView } from './PackagePlanView'
import { BundleReportView } from './ApplyBundleModal'

const Section = styled(Card)`display: flex; flex-direction: column; gap: 10px;`
const Title = styled.div`font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.md};`
const Hint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const TargetGrid = styled.div`
  display: grid; grid-template-columns: 140px 1fr 2fr 1fr 1fr auto; gap: 8px; align-items: center;
  & .h { color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  & .opts { grid-column: 2 / -1; display: flex; gap: 16px; align-items: center; color: ${colors.text.secondary}; font-size: ${fontSize.sm}; margin-bottom: 6px; }
`
const HistRow = styled.button`
  display: grid; grid-template-columns: 18px 150px 110px 140px 1fr 90px; gap: 10px; align-items: center; width: 100%;
  padding: 7px 4px; border: none; border-bottom: 1px solid ${colors.border}; background: transparent; cursor: pointer; text-align: left;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:hover { background: var(--hover-subtle); }
  & .mono { font-family: ${fonts.mono}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`

type Row = PromotionTarget & { name: string }

export function PromotionPanel() {
  const { t } = useTranslation()
  const [rows, setRows] = useState<Row[] | null>(null)
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[] | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [opened, setOpened] = useState<HistoryEntry | null>(null)

  useEffect(() => {
    getTargets().then((ts) => setRows(Object.entries(ts).map(([name, v]) => ({ name, ...v }))))
      .catch((e) => { setRows([]); setMsg({ tone: 'error', text: e instanceof ApiError ? e.message : String(e) }) })
    getHistory().then(setHistory).catch(() => setHistory([]))
  }, [])

  const set = (i: number, patch: Partial<Row>) => { setRows((rs) => rs!.map((r, j) => (j === i ? { ...r, ...patch } : r))); setDirty(true) }
  const add = () => { setRows((rs) => [...(rs ?? []), { name: '', label: '', url: 'https://', username: '', password: '', verify_ssl: true }]); setDirty(true) }
  const remove = (i: number) => { setRows((rs) => rs!.filter((_, j) => j !== i)); setDirty(true) }

  async function save() {
    if (!rows) return
    setSaving(true); setMsg(null)
    try {
      const body: Record<string, PromotionTarget> = {}
      for (const { name, ...rest } of rows) body[name.trim()] = rest
      const r = await saveTargets(body)
      setRows(Object.entries(r.targets).map(([name, v]) => ({ name, ...v })))
      setDirty(false); setMsg({ tone: 'ok', text: t('settings.promotion.saved') })
    } catch (e) {
      setMsg({ tone: 'error', text: e instanceof ApiError ? e.message : String(e) })
    } finally { setSaving(false) }
  }

  async function test(name: string) {
    setTesting(name); setMsg(null)
    try {
      const r = await testTarget(name)
      setMsg({ tone: 'ok', text: t('settings.promotion.testOk', { name: r.name ?? name, version: r.version ?? '?' }) })
    } catch (e) {
      setMsg({ tone: 'error', text: e instanceof ApiError ? e.message : String(e) })
    } finally { setTesting(null) }
  }

  async function open(id: string) {
    if (openId === id) { setOpenId(null); setOpened(null); return }
    setOpenId(id); setOpened(null)
    try { setOpened(await getHistoryEntry(id)) } catch { setOpened(null) }
  }

  if (!rows) return <SpinnerRing size={16} thickness={2} />
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Section>
        <Title>{t('settings.promotion.targets')}</Title>
        <Hint>{t('settings.promotion.targetsHint')}</Hint>
        {rows.length > 0 && (
          <TargetGrid>
            <span className="h">{t('settings.promotion.name')}</span>
            <span className="h">{t('settings.promotion.label')}</span>
            <span className="h">{t('settings.promotion.url')}</span>
            <span className="h">{t('settings.promotion.username')}</span>
            <span className="h">{t('settings.promotion.password')}</span>
            <span />
            {rows.map((r, i) => (
              <div key={i} style={{ display: 'contents' }}>
                <Input value={r.name} onChange={(e) => set(i, { name: e.target.value })} placeholder="prod" />
                <Input value={r.label ?? ''} onChange={(e) => set(i, { label: e.target.value })} placeholder="Production" />
                <Input value={r.url} onChange={(e) => set(i, { url: e.target.value })} placeholder="https://liberty.example.com" />
                <Input value={r.username ?? ''} onChange={(e) => set(i, { username: e.target.value })} placeholder="admin" />
                <PasswordInput value={r.password ?? ''} onChange={(e) => set(i, { password: e.target.value })} />
                <span style={{ display: 'flex', gap: 4 }}>
                  <Button $size="sm" $variant="ghost" disabled={dirty || testing !== null || !r.name} title={t('settings.promotion.test')} onClick={() => void test(r.name)}>
                    {testing === r.name ? <SpinnerRing size={12} thickness={2} /> : <Plug size={13} />} {t('settings.promotion.test')}
                  </Button>
                  <Button $size="sm" $variant="ghost" title={t('settings.promotion.remove')} onClick={() => remove(i)}><Trash2 size={13} /></Button>
                </span>
                <div className="opts">
                  <Checkbox checked={r.verify_ssl !== false} onChange={(v) => set(i, { verify_ssl: v })} label={t('settings.promotion.verifySsl')} />
                  <span>{t('settings.promotion.timeout')}</span>
                  <Input style={{ width: 90 }} type="number" min={5} max={3600} value={r.timeout_seconds ?? 120}
                    onChange={(e) => set(i, { timeout_seconds: Number(e.target.value) || 120 })} />
                </div>
              </div>
            ))}
          </TargetGrid>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <Button $size="sm" $variant="ghost" onClick={add}><Plus size={13} /> {t('settings.promotion.add')}</Button>
          <Button $size="sm" $variant="primary" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? <SpinnerRing size={12} thickness={2} /> : <Save size={13} />} {t('settings.promotion.save')}
          </Button>
        </div>
        {msg && <Banner $tone={msg.tone === 'ok' ? 'ok' : 'error'}>{msg.text}</Banner>}
      </Section>

      <Section>
        <Title>{t('settings.promotion.history')}</Title>
        {history && history.length === 0 && <Hint>{t('settings.promotion.historyEmpty')}</Hint>}
        {history?.map((h) => (
          <div key={h.id}>
            <HistRow type="button" onClick={() => void open(h.id)}>
              {openId === h.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <span>{new Date(h.at).toLocaleString()}</span>
              <span className="mono">{h.user}</span>
              <span className="mono" title={h.target_url}>{h.target}{h.target_version ? ` · ${h.target_version}` : ''}</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {h.payload === 'changes' ? t('settings.promotion.payloadChanges') : t('settings.promotion.payloadConfig')} · {h.label}
                {h.comment ? <span style={{ color: colors.text.muted }}> — {h.comment}</span> : null}
              </span>
              <Tag $tone={h.status === 'applied' ? 'green' : 'orange'}>{h.status}</Tag>
            </HistRow>
            {openId === h.id && opened?.report && (
              <div style={{ padding: '10px 0 14px 22px' }}>
                {isPlan(opened.report)
                  ? <PackagePlanView plan={opened.report} />
                  : <BundleReportView report={opened.report} force={new Set()} onToggleForce={() => {}} />}
              </div>
            )}
          </div>
        ))}
      </Section>
    </div>
  )
}
