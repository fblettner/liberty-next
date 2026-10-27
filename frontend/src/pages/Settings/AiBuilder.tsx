// AI settings — `ai.toml`, the assistant material an application SHIPS: starter questions
// for the app and for each of its screens (playbooks next). A scope bar picks the app; the
// page edits that app's block in place and saves the file whole (PUT /admin/config/ai/parsed
// + reload). The operator's own questions live in App → AI Assistant and win over these.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, Sparkles, LayoutList, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, SearchSelect, SpinnerRing, type SearchSelectOption } from '../../common'
import type { AiDoc, AppAiBody, ScreensDoc } from '../../types/config'
import { QuestionsList } from './AiStartersEditor'
import { ScopeBar, type ScopeOption } from './ScopeBar'
import { AddScopeModal } from './AddScopeModal'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts } from '../../theme'

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; height: 100%;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const Body = styled.div`flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;`
const Section = styled(Card)`padding: 10px 14px; display: flex; flex-direction: column; gap: 8px;`
const SectionHead = styled.div`display: flex; align-items: center; gap: 8px; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; & .spacer { flex: 1; }`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const ScreenHead = styled.div`
  display: flex; align-items: center; gap: 8px; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & .id { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & .spacer { flex: 1; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 12px 4px; text-align: center;`

export default function AiBuilder() {
  const { t } = useTranslation()
  const { currentApp, connectors } = useWorkspace()
  const [scope, setScope] = useState('')
  const [doc, setDoc] = useState<Record<string, AppAiBody> | null>(null)
  const [saved, setSaved] = useState<Record<string, AppAiBody> | null>(null)
  const [screens, setScreens] = useState<ScreensDoc['screens']>({})
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [addScopeOpen, setAddScopeOpen] = useState(false)

  const load = () => {
    setError(null)
    Promise.all([
      api.get<AiDoc>('/admin/config/ai/parsed'),
      // The app's screens feed the "add a screen" picker; a failure just leaves it free-text.
      api.get<ScreensDoc>('/admin/config/screens/parsed').catch((): ScreensDoc => ({ path: '', screens: {} })),
    ])
      .then(([d, sc]) => { setDoc(d.ai); setSaved(d.ai); setScreens(sc.screens) })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const scopes = useMemo<ScopeOption[]>(() => Object.keys(doc ?? {}).sort().map((v) => ({ value: v, label: v })), [doc])
  useEffect(() => {
    if (!doc) return
    setScope((cur) => (cur && scopes.some((s) => s.value === cur)) ? cur : (currentApp && scopes.some((s) => s.value === currentApp)) ? currentApp : (scopes[0]?.value ?? ''))
  }, [doc, scopes, currentApp])
  const addCandidates = useMemo(() => {
    const have = new Set(Object.keys(doc ?? {}))
    return (connectors ?? []).filter((c) => !have.has(c.name)).map((c) => c.name).sort()
  }, [doc, connectors])

  const block: AppAiBody = doc?.[scope] ?? {}
  const dirty = JSON.stringify(doc) !== JSON.stringify(saved)
  const update = (patch: Partial<AppAiBody>) => setDoc((p) => (p && scope ? { ...p, [scope]: { ...(p[scope] ?? {}), ...patch } } : p))
  const screenIds = Object.keys(block.screens ?? {})
  const appScreens = screens[scope] ?? {}
  const screenOpts: SearchSelectOption[] = Object.entries(appScreens)
    .filter(([id]) => !screenIds.includes(id))
    .map(([id, s]) => ({ value: id, label: (typeof s?.label === 'string' && s.label) || id, mono: id }))
    .sort((a, b) => a.value.localeCompare(b.value))
  const setScreen = (id: string, starters: string[]) => update({ screens: { ...(block.screens ?? {}), [id]: { starters } } })
  const dropScreen = (id: string) => { const next = { ...(block.screens ?? {}) }; delete next[id]; update({ screens: next }) }

  const persist = async () => {
    if (!doc) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put('/admin/config/ai/parsed', { ai: doc })
      await api.post('/admin/reload')
      setSaved(doc)
      setStatus(t('settings.ai.saved'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Centered />

  return (
    <Shell>
      <ScopeBar scopes={scopes} value={scope} onChange={setScope} label={t('settings.ai.app')}
        emptyHint={t('settings.ai.noScopes')} onAddScope={() => setAddScopeOpen(true)} />
      <Toolbar>
        <Sparkles size={14} style={{ color: colors.text.muted }} />
        <Hint>{t('settings.ai.hint')}</Hint>
        <span style={{ flex: 1 }} />
        {busy && <SpinnerRing size={14} thickness={2} />}
        {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        <Button $variant="primary" $size="sm" onClick={() => void persist()} disabled={busy || !dirty}><Save size={13} /> {t('common.save')}</Button>
      </Toolbar>
      {scope && (
        <Body>
          <Section>
            <SectionHead><Sparkles size={14} /> {t('settings.ai.appStarters', { app: scope })}</SectionHead>
            <QuestionsList value={block.starters ?? []} onChange={(starters) => update({ starters })} />
          </Section>
          <Section>
            <SectionHead><LayoutList size={14} /> {t('settings.ai.screenStarters')} <span className="spacer" />
              <div style={{ minWidth: 260 }}>
                <SearchSelect value="" options={screenOpts} allowCustom placeholder={t('settings.ai.addScreen')}
                  onChange={(v) => { const id = v.trim(); if (id && !screenIds.includes(id)) setScreen(id, []) }} />
              </div>
            </SectionHead>
            {screenIds.length === 0 && <Empty>{t('settings.ai.noScreens')}</Empty>}
            {screenIds.map((id) => (
              <Section key={id}>
                <ScreenHead>
                  <span className="id">{id}</span>
                  {typeof appScreens[id]?.label === 'string' && <span>— {appScreens[id].label as string}</span>}
                  <span className="spacer" />
                  <Button $variant="ghost" $size="sm" onClick={() => dropScreen(id)} style={{ color: colors.red.main }} title={t('common.remove', 'Remove')}><Trash2 size={12} /></Button>
                </ScreenHead>
                <QuestionsList value={block.screens?.[id]?.starters ?? []} onChange={(starters) => setScreen(id, starters)} />
              </Section>
            ))}
          </Section>
        </Body>
      )}
      {addScopeOpen && (
        <AddScopeModal candidates={addCandidates} title={t('settings.ai.addScope')} emptyHint={t('settings.ai.addScopeEmpty')}
          onPick={(c) => { setDoc((p) => ({ ...(p ?? {}), [c]: p?.[c] ?? {} })); setScope(c); setStatus(null) }}
          onClose={() => setAddScopeOpen(false)} />
      )}
    </Shell>
  )
}
