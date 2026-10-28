// Screen Assistant → "A module" (catalogue v2): pick a module of the catalogue (``[[modules]]`` beside
// the presets), see what applying it does on the live tables — per screen its table, the column prefix
// found, the columns, the data items the table lacks, new / exists / replace — then apply: the read
// queries, the read-only screens with their detail tabs, the menu folder and the dictionary entries
// (liberty/module_apply.py). Replace rewrites the module's own entries that exist.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { Check, Layers } from 'lucide-react'
import { ApiError } from '../../api/client'
import { Banner, Button, Checkbox, Input, SpinnerRing, Tag, type Tone } from '../../common'
import { ModalBody, ModalFooter } from '../../common/Modal'
import { applyModule, getModules, groupModules, previewModule, screenTree, writes } from '../../services/modules'
import type { EntryStatus, ModuleApplyResult, ModulePlan, ModuleSummary } from '../../types/modules'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

const STATUS_TONE: Record<EntryStatus, Tone> = { new: 'green', replace: 'orange', exists: 'neutral' }

const Split = styled.div`display: grid; grid-template-columns: minmax(220px, 300px) minmax(0, 1fr); gap: 16px; height: 100%; min-height: 0;
  @media (max-width: 860px) { grid-template-columns: 1fr; }`
const List = styled.div`display: flex; flex-direction: column; gap: 6px; overflow: auto; min-height: 0;`
const Group = styled.div`font-size: ${fontSize.micro}; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase; color: ${colors.text.muted}; margin: 8px 2px 2px;`
const Card = styled.button<{ $on: boolean }>`
  text-align: left; padding: 9px 11px; border-radius: ${radius.md}; cursor: pointer; font-family: ${fonts.sans};
  border: 1px solid ${({ $on }) => ($on ? colors.blue.border : colors.border)}; background: ${({ $on }) => ($on ? colors.blue.bg : colors.bg.card)};
  & .lbl { font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.sm}; }
  & .sub { color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin-top: 3px; }
`
const Detail = styled.div`display: flex; flex-direction: column; gap: 12px; overflow: auto; min-height: 0;`
const Head = styled.div`display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  & .t { font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; }
  & .m { color: ${colors.text.muted}; font-size: ${fontSize.sm}; }`
const Muted = styled.div`color: ${colors.text.secondary}; font-size: ${fontSize.sm}; line-height: 1.5;`
const Table = styled.table`width: 100%; border-collapse: collapse; font-size: ${fontSize.sm};
  & th { text-align: left; font-weight: 600; color: ${colors.text.muted}; padding: 6px 8px; border-bottom: 1px solid ${colors.border}; }
  & td { padding: 6px 8px; border-bottom: 1px solid ${colors.border}; color: ${colors.text.primary}; vertical-align: top; }
  & .mono { font-family: ${fonts.mono}; }
  & .num { text-align: right; }`

export default function ModuleApply({ onClose }: { onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { refresh } = useWorkspace()
  const [modules, setModules] = useState<ModuleSummary[] | null>(null)
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<string>('')
  const [replace, setReplace] = useState(false)
  const [plan, setPlan] = useState<ModulePlan | null>(null)
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ModuleApplyResult | null>(null)

  useEffect(() => {
    getModules().then((m) => { setModules(m); setPicked((p) => p || m[0]?.id || '') })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  const module = modules?.find((m) => m.id === picked) ?? null
  const groups = useMemo(() => groupModules(modules ?? [], q, t('assistant.module.other')), [modules, q, t])
  const lang = i18n.language?.slice(0, 2) ?? 'en'
  const label = (m: { label: string; l?: Record<string, string> }) => m.l?.[lang] ?? m.label

  // the preview reads the live tables: run on every pick / replace switch, the last one wins
  useEffect(() => {
    if (!picked) return
    let gone = false
    setPlan(null); setResult(null); setError(null); setBusy('preview')
    previewModule(picked, replace)
      .then((p) => { if (!gone) setPlan(p) })
      .catch((e) => { if (!gone) setError(e instanceof ApiError ? e.message : String(e)) })
      .finally(() => { if (!gone) setBusy(null) })
    return () => { gone = true }
  }, [picked, replace])

  const apply = async () => {
    if (!picked) return
    setBusy('apply'); setError(null)
    try {
      const r = await applyModule(picked, replace)
      setResult(r); setPlan(r)
      refresh()                          // the new menu folder and screens reach the sidebar
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const byScreen = new Map((plan?.screens ?? []).map((s) => [s.id, s]))
  const firstScreen = plan?.menu.items[0]?.id
  const status = (s: EntryStatus) => <Tag $tone={STATUS_TONE[s]}>{t(`assistant.module.status.${s}`)}</Tag>

  return (
    <>
      <ModalBody style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {modules === null && !error ? <SpinnerRing /> : (
          <Split>
            <List>
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('assistant.module.search')} />
              {groups.length === 0 && <Muted style={{ padding: 12 }}>{t('assistant.module.none')}</Muted>}
              {groups.map(([g, list]) => (
                <div key={g}>
                  <Group>{g}</Group>
                  {list.map((m) => (
                    <Card key={m.id} type="button" $on={m.id === picked} onClick={() => setPicked(m.id)} style={{ width: '100%', marginBottom: 6 }}>
                      <div className="lbl">{label(m)}</div>
                      <div className="sub">{m.app} · {m.connector} · {t('assistant.module.screensN', { n: m.screens.length })}</div>
                    </Card>
                  ))}
                </div>
              ))}
            </List>
            <Detail>
              {error && <Banner $tone="error">{error}</Banner>}
              {module && (
                <>
                  <Head>
                    <Layers size={16} style={{ color: colors.blue.main, alignSelf: 'center' }} />
                    <span className="t">{label(module)}</span>
                    <span className="m">{t('assistant.module.into', { app: module.app, connector: module.connector })}</span>
                  </Head>
                  {module.description && <Muted>{module.description}</Muted>}
                  {busy === 'preview' && <Muted><SpinnerRing size={12} /> {t('assistant.module.reading')}</Muted>}
                  {plan && (
                    <>
                      {plan.errors.length > 0 && (
                        <Banner $tone="error">{t('assistant.module.blocked')}<ul style={{ margin: '6px 0 0 18px' }}>{plan.errors.map((e) => <li key={e}>{e}</li>)}</ul></Banner>
                      )}
                      <Table>
                        <thead><tr>
                          <th>{t('assistant.module.screen')}</th><th>{t('assistant.module.table')}</th><th>{t('assistant.module.prefix')}</th>
                          <th className="num">{t('assistant.module.columns')}</th><th>{t('assistant.module.missing')}</th><th>{t('assistant.module.state')}</th>
                        </tr></thead>
                        <tbody>
                          {screenTree(module).map(({ id, depth }) => {
                            const s = byScreen.get(id)
                            const m = module.screens.find((x) => x.id === id)
                            return (
                              <tr key={`${id}:${depth}`}>
                                <td style={{ paddingLeft: 8 + depth * 18 }}>{depth > 0 && <span style={{ color: colors.text.muted }}>↳ </span>}{m?.label ?? id} <span className="mono" style={{ color: colors.text.muted }}>{id}</span></td>
                                <td className="mono">{s?.table ?? m?.table}</td>
                                <td className="mono">{s?.prefix ?? '—'}</td>
                                <td className="num">{s?.columns ?? '—'}</td>
                                <td className="mono" style={{ color: s?.missing.length ? colors.orange.main : colors.text.muted }}>{s?.error ?? (s?.missing.join(', ') || '—')}</td>
                                <td>{s && status(s.status)}</td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </Table>
                      <Muted>
                        {t('assistant.module.menu')} {status(plan.menu.folder)}{' '}
                        {plan.menu.items.map((i) => <span key={i.id} style={{ marginRight: 6 }}><span className="mono">{i.id}</span> {status(i.status)}</span>)}
                      </Muted>
                      <Checkbox checked={replace} onChange={setReplace} disabled={busy !== null}
                        label={<span>{t('assistant.module.replace')} <span style={{ color: colors.text.muted }}>— {t('assistant.module.replaceHint')}</span></span>} />
                      {result && (
                        <Banner $tone="ok">
                          {t('assistant.module.done', { queries: result.new.queries.length, screens: result.new.screens.length, menu: result.new.menu_items.length, dictionary: result.dictionary.created.length })}
                          {result.dictionary.skipped.length > 0 && <div>{t('assistant.module.skipped')}: {result.dictionary.skipped.join('; ')}</div>}
                        </Banner>
                      )}
                      {!result && plan.errors.length === 0 && writes(plan) === 0 && <Muted>{t('assistant.module.nothing')}</Muted>}
                    </>
                  )}
                </>
              )}
            </Detail>
          </Split>
        )}
      </ModalBody>
      <ModalFooter style={{ justifyContent: 'space-between' }}>
        <Button $size="sm" $variant="ghost" disabled={busy === 'apply'} onClick={onClose}>{result ? t('common.close') : t('common.cancel')}</Button>
        {result && module && firstScreen
          ? <Button $size="sm" $variant="primary" onClick={() => { navigate(`/screen/${encodeURIComponent(module.app)}/${encodeURIComponent(firstScreen)}`); onClose() }}>{t('assistant.openScreen')}</Button>
          : <Button $size="sm" $variant="primary" onClick={apply}
              disabled={!plan || busy !== null || plan.errors.length > 0 || writes(plan) === 0}>
              {busy === 'apply' ? <SpinnerRing size={14} /> : <Check size={14} />} {t('assistant.module.apply')}
            </Button>}
      </ModalFooter>
    </>
  )
}
