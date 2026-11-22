// AI settings — `ai.toml`, the assistant material an application SHIPS: starter questions
// for the app and for each of its screens, playbooks, and tools (a plugin function each). A scope bar picks the app; the
// page edits that app's block in place and saves the file whole (PUT /admin/config/ai/parsed
// + reload). The operator's own questions live in App → AI Assistant and win over these.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, Sparkles, LayoutList, Trash2, BookOpen, Plus, Edit3, Wrench, Undo2, Languages, ListTree } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { failureOf, targetOf, type SaveFailure, type SaveProblem } from '../../services/saveProblems'
import { Banner, Button, Card, Centered, Input, SaveProblems, SearchSelect, SpinnerRing, languageName, useModals, useOtherLanguages, type SearchSelectOption, HeaderActions } from '../../common'
import type { AiDoc, AiLocaleBody, AiToolBody, AppAiBody, PlaybookBody, ScreensDoc, StarterGroupBody } from '../../types/config'
import { starterView, withStarters } from '../../services/aiStarters'
import { QuestionsList } from './AiStartersEditor'
import { AiPlaybookModal } from './AiPlaybookModal'
import { AiToolModal } from './AiToolModal'
import { ScopeBar, type ScopeOption } from './ScopeBar'
import { AddScopeModal } from './AddScopeModal'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts } from '../../theme'
import { BuilderShell as Shell } from '../../common/builderShell'
import { useDirtyGuard } from '../../tabs/dirtyGuard'

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
const Item = styled.div`
  display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-radius: 10px;
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer; min-width: 0;
  & > .icon { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; }
  & .meta { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .actions { flex-shrink: 0; display: flex; gap: 4px; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`

export default function AiBuilder() {
  const { t, i18n } = useTranslation()
  const modals = useModals()
  const { focusApp: currentApp, connectors } = useWorkspace()
  const [scope, setScope] = useState('')
  // the language whose questions are shown: '' = as written, else the lists under `l.<language>`
  const [lang, setLang] = useState('')
  const [editing, setEditing] = useState<(PlaybookBody & { id: string }) | null | undefined>(undefined)
  const [editingTool, setEditingTool] = useState<(AiToolBody & { id: string }) | null | undefined>(undefined)
  const [doc, setDoc] = useState<Record<string, AppAiBody> | null>(null)
  const [saved, setSaved] = useState<Record<string, AppAiBody> | null>(null)
  // a save the server refused: its problems one by one
  const [failure, setFailure] = useState<SaveFailure | null>(null)
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
  useDirtyGuard(dirty)          // the shell asks before a closed tab or a reload loses these edits
  const update = (patch: Partial<AppAiBody>) => setDoc((p) => (p && scope ? { ...p, [scope]: { ...(p[scope] ?? {}), ...patch } } : p))
  const screenIds = Object.keys(block.screens ?? {})
  const appScreens = screens[scope] ?? {}
  const screenOpts: SearchSelectOption[] = Object.entries(appScreens)
    .filter(([id]) => !screenIds.includes(id))
    .map(([id, s]) => ({ value: id, label: (typeof s?.label === 'string' && s.label) || id, mono: id }))
    .sort((a, b) => a.value.localeCompare(b.value))
  // The questions shown are those of the picked language: as written, or what `l.<language>` says
  // (a list left empty there falls back on the list as written).
  const languages = useOtherLanguages(Object.keys(block.l ?? {}))
  const shown = lang && languages.includes(lang) ? lang : ''
  const view: AiLocaleBody = starterView(block, shown)
  const updateView = (patch: Partial<AiLocaleBody>) => setDoc((p) => (p && scope ? { ...p, [scope]: withStarters(p[scope] ?? {}, shown, patch) } : p))
  const groups: StarterGroupBody[] = view.groups ?? []
  const setGroup = (i: number, patch: Partial<StarterGroupBody>) => updateView({ groups: groups.map((g, j) => (j === i ? { ...g, ...patch } : g)) })
  const setScreen = (id: string, starters: string[]) => updateView({ screens: { ...(view.screens ?? {}), [id]: { ...(view.screens?.[id] ?? {}), starters } } })
  // a screen's questions under headings (the same shape as the application's): none → the key leaves the file
  const setScreenGroups = (id: string, list: StarterGroupBody[]) => {
    const { groups: _dropped, ...rest } = view.screens?.[id] ?? {}
    updateView({ screens: { ...(view.screens ?? {}), [id]: list.length ? { ...rest, groups: list } : rest } })
  }
  // a screen taken out leaves every language with it
  const dropScreen = (id: string) => setDoc((p) => {
    if (!p || !scope) return p
    let next: AppAiBody = { ...(p[scope] ?? {}) }
    const own = { ...(next.screens ?? {}) }; delete own[id]; next.screens = own
    for (const l of Object.keys(next.l ?? {})) {
      const said = { ...(next.l?.[l]?.screens ?? {}) }
      if (id in said) { delete said[id]; next = withStarters(next, l, { screens: said }) }
    }
    return { ...p, [scope]: next }
  })
  const playbookIds = Object.keys(block.playbooks ?? {})
  const savePlaybook = (id: string, body: PlaybookBody) => { update({ playbooks: { ...(block.playbooks ?? {}), [id]: body } }); setEditing(undefined) }
  const dropPlaybook = async (id: string) => {
    if (!(await modals.confirm({ title: t('common.delete'), message: t('settings.ai.deletePlaybook', { id }), variant: 'danger' }))) return
    const next = { ...(block.playbooks ?? {}) }; delete next[id]; update({ playbooks: next })
  }

  const toolIds = Object.keys(block.tools ?? {})
  const saveTool = (id: string, body: AiToolBody) => { update({ tools: { ...(block.tools ?? {}), [id]: body } }); setEditingTool(undefined) }
  const dropTool = async (id: string) => {
    if (!(await modals.confirm({ title: t('common.delete'), message: t('settings.ai.deleteTool', { id: `${scope}_${id}` }), variant: 'danger' }))) return
    const next = { ...(block.tools ?? {}) }; delete next[id]; update({ tools: next })
  }

  const persist = async () => {
    if (!doc) return
    setBusy(true); setError(null); setStatus(null); setFailure(null)
    try {
      await api.put('/admin/config/ai/parsed', { ai: doc })
      await api.post('/admin/reload')
      setSaved(doc)
      setStatus(t('settings.ai.saved'))
    } catch (e) {
      setFailure(failureOf(e))
    } finally { setBusy(false) }
  }
  // "Open" on a problem of a refused save: the application it is in.
  const problemOpener = (p: SaveProblem): (() => void) | null => {
    const app = targetOf('ai', p.path)?.scope
    return app && doc?.[app] && app !== scope ? () => setScope(app) : null
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Centered />

  return (
    <Shell>
      <ScopeBar scopes={scopes} value={scope} onChange={setScope} label={t('settings.ai.app')}
        emptyHint={t('settings.ai.noScopes')} onAddScope={() => setAddScopeOpen(true)} />
      {/* Discard / Save are in the page header, before Reload config */}
      <HeaderActions>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        <Button $variant="ghost" $size="sm" onClick={() => { setDoc(saved); setStatus(null) }} disabled={busy || !dirty}><Undo2 size={13} /> {t('common.discard')}</Button>
        <Button $variant="primary" $size="sm" onClick={() => void persist()} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
        </Button>
      </HeaderActions>
      <Hint>{t('settings.ai.hint')}</Hint>
      {status && <Banner $tone="ok">{status}</Banner>}
      {error && <Banner $tone="error">{error}</Banner>}
      <SaveProblems failure={failure} opener={problemOpener} />
      {scope && (
        <Body>
          {languages.length > 0 && (
            <Section>
              <SectionHead><Languages size={14} /> {t('settings.ai.language')} <span className="spacer" />
                <div style={{ minWidth: 260 }}>
                  <SearchSelect value={shown} onChange={setLang} anyLabel={t('settings.ai.languageWritten')} placeholder={t('settings.ai.languageWritten')}
                    options={languages.map((l) => ({ value: l, label: languageName(l, i18n.language), mono: l }))} />
                </div>
              </SectionHead>
              <Hint>{shown ? t('settings.ai.languageHint', { language: languageName(shown, i18n.language) }) : t('settings.ai.languageWrittenHint')}</Hint>
            </Section>
          )}
          <Section>
            <SectionHead><Sparkles size={14} /> {t('settings.ai.appStarters', { app: scope })}</SectionHead>
            <QuestionsList value={view.starters ?? []} onChange={(starters) => updateView({ starters })} />
          </Section>
          <Section>
            <SectionHead><ListTree size={14} /> {t('settings.ai.groups')} <span className="spacer" />
              <Button $variant="ghost" $size="sm" onClick={() => updateView({ groups: [...groups, { label: t('settings.ai.groupNew'), starters: [] }] })}><Plus size={13} /> {t('settings.ai.addGroup')}</Button>
            </SectionHead>
            <Hint>{t('settings.ai.groupsHint')}</Hint>
            {groups.length === 0 && <Empty>{t('settings.ai.noGroups')}</Empty>}
            {groups.map((g, i) => (
              <Section key={i}>
                <ScreenHead>
                  <Input value={g.label} onChange={(e) => setGroup(i, { label: e.target.value })} placeholder={t('settings.ai.groupLabel')} style={{ maxWidth: 320 }} />
                  <span className="spacer" />
                  <Button $variant="ghost" $size="sm" onClick={() => updateView({ groups: groups.filter((_, j) => j !== i) })} style={{ color: colors.red.main }} title={t('common.remove')}><Trash2 size={12} /></Button>
                </ScreenHead>
                <QuestionsList value={g.starters ?? []} onChange={(starters) => setGroup(i, { starters })} />
              </Section>
            ))}
          </Section>
          <Section>
            <SectionHead><LayoutList size={14} /> {t('settings.ai.screenStarters')} <span className="spacer" />
              <div style={{ minWidth: 260 }}>
                <SearchSelect sort value="" options={screenOpts} allowCustom placeholder={t('settings.ai.addScreen')}
                  onChange={(v) => { const id = v.trim(); if (id && !screenIds.includes(id)) update({ screens: { ...(block.screens ?? {}), [id]: { starters: [] } } }) }} />
              </div>
            </SectionHead>
            {screenIds.length === 0 && <Empty>{t('settings.ai.noScreens')}</Empty>}
            {screenIds.map((id) => (
              <Section key={id}>
                <ScreenHead>
                  <span className="id">{id}</span>
                  {typeof appScreens[id]?.label === 'string' && <span>— {appScreens[id].label as string}</span>}
                  <span className="spacer" />
                  <Button $variant="ghost" $size="sm" onClick={() => dropScreen(id)} style={{ color: colors.red.main }} title={t('common.remove')}><Trash2 size={12} /></Button>
                </ScreenHead>
                <QuestionsList value={view.screens?.[id]?.starters ?? []} onChange={(starters) => setScreen(id, starters)} />
                {/* the screen's own headings: shown in place of its plain questions when there are some */}
                <div data-screen-groups={id} style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
                  {(view.screens?.[id]?.groups ?? []).map((g, i) => (
                    <Section key={i}>
                      <ScreenHead>
                        <Input value={g.label} onChange={(e) => setScreenGroups(id, (view.screens?.[id]?.groups ?? []).map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
                          placeholder={t('settings.ai.groupLabel')} style={{ maxWidth: 320 }} />
                        <span className="spacer" />
                        <Button $variant="ghost" $size="sm" onClick={() => setScreenGroups(id, (view.screens?.[id]?.groups ?? []).filter((_, j) => j !== i))} style={{ color: colors.red.main }} title={t('common.remove')}><Trash2 size={12} /></Button>
                      </ScreenHead>
                      <QuestionsList value={g.starters ?? []} onChange={(starters) => setScreenGroups(id, (view.screens?.[id]?.groups ?? []).map((x, j) => (j === i ? { ...x, starters } : x)))} />
                    </Section>
                  ))}
                  <div>
                    <Button $variant="ghost" $size="sm" onClick={() => setScreenGroups(id, [...(view.screens?.[id]?.groups ?? []), { label: t('settings.ai.groupNew'), starters: [] }])}>
                      <Plus size={13} /> {t('settings.ai.addScreenGroup')}
                    </Button>
                  </div>
                </div>
              </Section>
            ))}
          </Section>
          <Section>
            <SectionHead><BookOpen size={14} /> {t('settings.ai.playbooks')} <span className="spacer" />
              <Button $variant="ghost" $size="sm" onClick={() => setEditing(null)} disabled={busy}><Plus size={13} /> {t('settings.ai.addPlaybook')}</Button>
            </SectionHead>
            <Hint>{t('settings.ai.playbooksHint')}</Hint>
            {playbookIds.length === 0 && <Empty>{t('settings.ai.noPlaybooks')}</Empty>}
            {playbookIds.map((id) => {
              const pb = block.playbooks![id]
              return (
                <Item key={id} onClick={() => setEditing({ ...pb, id })}>
                  <BookOpen className="icon" size={16} />
                  <span className="text">
                    <span className="name">{id}<span style={{ color: colors.text.secondary, fontFamily: fonts.sans }}> — {pb.title}</span></span>
                    {pb.description && <span className="meta">{pb.description}</span>}
                  </span>
                  <span className="actions" onClick={(ev) => ev.stopPropagation()}>
                    <Button $variant="ghost" $size="sm" onClick={() => setEditing({ ...pb, id })} disabled={busy}><Edit3 size={13} /> {t('common.edit')}</Button>
                    <Button $variant="ghost" $size="sm" onClick={() => void dropPlaybook(id)} disabled={busy} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('common.delete')}</Button>
                  </span>
                </Item>
              )
            })}
          </Section>
          <Section>
            <SectionHead><Wrench size={14} /> {t('settings.ai.tools')} <span className="spacer" />
              <Button $variant="ghost" $size="sm" onClick={() => setEditingTool(null)} disabled={busy || scope === '*'}><Plus size={13} /> {t('settings.ai.addTool')}</Button>
            </SectionHead>
            <Hint>{t('settings.ai.toolsHint')}</Hint>
            {toolIds.length === 0 && <Empty>{t('settings.ai.noTools')}</Empty>}
            {toolIds.map((id) => {
              const tool = block.tools![id]
              return (
                <Item key={id} onClick={() => setEditingTool({ ...tool, id })}>
                  <Wrench className="icon" size={16} />
                  <span className="text">
                    <span className="name">{scope}_{id}<span style={{ color: colors.text.secondary }}> — {tool.callable}</span></span>
                    <span className="meta">{tool.description}</span>
                  </span>
                  <span className="actions" onClick={(ev) => ev.stopPropagation()}>
                    <Button $variant="ghost" $size="sm" onClick={() => setEditingTool({ ...tool, id })} disabled={busy}><Edit3 size={13} /> {t('common.edit')}</Button>
                    <Button $variant="ghost" $size="sm" onClick={() => void dropTool(id)} disabled={busy} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('common.delete')}</Button>
                  </span>
                </Item>
              )
            })}
          </Section>
        </Body>
      )}
      {editingTool !== undefined && (
        <AiToolModal app={scope} initial={editingTool} takenIds={toolIds} onSave={saveTool} onClose={() => setEditingTool(undefined)} />
      )}
      {editing !== undefined && (
        <AiPlaybookModal initial={editing} takenIds={playbookIds} onSave={savePlaybook} onClose={() => setEditing(undefined)} />
      )}
      {addScopeOpen && (
        <AddScopeModal candidates={addCandidates} title={t('settings.ai.addScope')} emptyHint={t('settings.ai.addScopeEmpty')}
          onPick={(c) => { setDoc((p) => ({ ...(p ?? {}), [c]: p?.[c] ?? {} })); setScope(c); setStatus(null) }}
          onClose={() => setAddScopeOpen(false)} />
      )}
    </Shell>
  )
}
