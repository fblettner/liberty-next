// Liberty Next → Administer → Theme — the installation's look (superuser): the accent colour (a palette to click,
// or one's own), the application's name, the font and the text size, and — for who needs them — raw CSS
// variables. Written to theme.toml on save and served by GET /api/theme to every client. Dark / light stays
// each user's toggle: the preview beside shows both, on a small piece of the application.
//
// The draft's logic (file ⇄ draft, the variables it resolves to) is in services/themeDraft.ts, tested; the
// preview in ThemePreview.tsx.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Check, ChevronDown, ChevronRight, Palette, Plus, Save, Tag as TagIcon, Type, Undo2, X } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, HeaderActions, Field, Input, SpinnerRing } from '../../common'
import { useBranding, derivePrimaryVars } from '../../branding/BrandingContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import {
  SIZE_STEPS, docFromDraft, draftFromDoc, draftVars, effectiveFont, effectivePrimary, fullHex, isHex,
  type ThemeDoc, type ThemeDraft, type ThemeFont, type ThemePreset,
} from '../../services/themeDraft'
import ThemePreview from './ThemePreview'

interface ThemeParsed { path: string; theme: ThemeDoc; presets: ThemePreset[]; fonts: ThemeFont[] }

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0;`
const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: flex-start; flex-wrap: wrap;`
const Settings = styled.div`flex: 0 0 min(500px, 100%); min-width: 0; display: flex; flex-direction: column; gap: 12px;`
const Beside = styled.div`flex: 1 1 420px; min-width: 0; display: flex; flex-direction: column; gap: 8px;`
const Part = styled(Card)`padding: 0; overflow: hidden;`
const PartHead = styled.div<{ $click?: boolean }>`
  display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; text-transform: uppercase; letter-spacing: 0.06em;
  cursor: ${(p) => (p.$click ? 'pointer' : 'default')};
  & svg { color: ${colors.blue.main}; }
  & .state { margin-left: auto; font-weight: 400; text-transform: none; letter-spacing: normal; color: ${colors.text.muted}; }
`
const PartBody = styled.div`padding: 14px; display: flex; flex-direction: column; gap: 12px;`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Choices = styled.div`display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 8px;`
const Choice = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 8px; padding: 8px 10px; text-align: left; cursor: pointer; min-width: 0;
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : colors.border)}; border-radius: ${radius.md};
  background: ${(p) => (p.$on ? colors.blue.bg : colors.bg.input)}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  & .dot { flex: 0 0 18px; height: 18px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: ${colors.text.inverse}; }
  & .aa { flex: 0 0 auto; font-size: ${fontSize.lg}; font-weight: 600; }
  & .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { border-color: ${(p) => (p.$on ? colors.blue.main : colors.blue.border)}; }
`
const ColorRow = styled.div`display: flex; align-items: center; gap: 8px;`
const Picker = styled.input`
  width: 38px; height: 32px; padding: 0; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: transparent; cursor: pointer;
`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden; align-self: flex-start;`
const SegBtn = styled.button<{ $on?: boolean }>`
  height: 32px; padding: 0 12px; border: none; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.base};
  background: ${(p) => (p.$on ? colors.blue.bg : colors.bg.input)}; color: ${(p) => (p.$on ? colors.blue.main : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
  &:hover { color: ${colors.text.primary}; }
`
const VarLine = styled.div`display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; gap: 6px; align-items: center;`
const IconBtn = styled.button`
  display: inline-flex; padding: 5px; border: none; border-radius: ${radius.sm}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { color: ${colors.red.main}; background: var(--hover-subtle); }
`
const AddBtn = styled.button`
  display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; padding: 5px 11px; cursor: pointer;
  border: 1px dashed ${colors.border}; border-radius: ${radius.md}; background: transparent; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.border}; }
`

const EMPTY: ThemeDraft = { preset: 'default', appName: '', primary: '', fontFamily: '', fontScale: 1, vars: [] }

export default function ThemeBuilder() {
  const { t } = useTranslation()
  const { applyVars, refresh } = useBranding()
  const [presets, setPresets] = useState<ThemePreset[]>([])
  const [fontList, setFontList] = useState<ThemeFont[]>([])
  const [draft, setDraft] = useState<ThemeDraft>(EMPTY)
  const [original, setOriginal] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setStatus(null)
    try {
      const r = await api.get<ThemeParsed>('/admin/config/theme/parsed')
      setPresets(r.presets)
      setFontList(r.fonts ?? [])
      const seed = draftFromDoc(r.theme)
      setDraft(seed)
      setOriginal(JSON.stringify(docFromDraft(seed)))
      setAdvanced(seed.vars.length > 0)        // what the file already holds is shown, not folded away
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { void load() }, [load])

  const set = (patch: Partial<ThemeDraft>) => { setDraft((d) => ({ ...d, ...patch })); setStatus(null) }
  const doc = useMemo(() => docFromDraft(draft), [draft])
  const dirty = JSON.stringify(doc) !== original
  const primary = effectivePrimary(draft, presets)
  const font = effectiveFont(draft, fontList)
  // the variables the draft resolves to: applied to the whole page while editing (put back on leaving), and
  // handed to the preview's two panes
  const vars = useMemo(() => draftVars(draft, presets, fontList, derivePrimaryVars), [draft, presets, fontList])
  useEffect(() => { if (!loading) applyVars(vars) }, [loading, vars, applyVars])
  useEffect(() => () => { void refresh() }, [refresh])

  const discard = () => {
    if (!original) return
    setDraft(draftFromDoc(JSON.parse(original) as ThemeDoc)); setStatus(null); setError(null)
  }
  const save = async () => {
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put('/admin/config/theme/parsed', { theme: doc })
      await refresh()
      setStatus(t('settings.theme.saved'))
      setOriginal(JSON.stringify(doc))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }
  const setVar = (i: number, at: 0 | 1, value: string) =>
    set({ vars: draft.vars.map((v, j) => (j === i ? (at === 0 ? [value, v[1]] : [v[0], value]) : v)) })

  if (loading) return <SpinnerRing size={16} thickness={2} />

  return (
    <Shell>
      {/* Save / Discard sit in the page header, before Reload config (in a row here when there is no header) */}
      <HeaderActions>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty}>
          <Undo2 size={13} /> {t('common.discard')}
        </Button>
        <Button $variant="primary" $size="sm" onClick={() => void save()} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
        </Button>
      </HeaderActions>
      <Hint>{t('settings.theme.intro')}</Hint>
      {status && <Banner $tone="ok">{status}</Banner>}
      {error && <Banner $tone="error">{error}</Banner>}

      <Split>
        <Settings>
          <Part>
            <PartHead><Palette size={14} /> {t('settings.theme.colour')}<span className="state">{primary}</span></PartHead>
            <PartBody>
              <Choices>
                {presets.map((p) => {
                  const on = draft.preset === p.id && !draft.primary
                  return (
                    <Choice key={p.id} type="button" $on={on} data-preset={p.id} onClick={() => set({ preset: p.id, primary: '' })}>
                      <span className="dot" style={{ background: p.primary }}>{on && <Check size={12} />}</span>
                      <span className="name">{p.label}</span>
                    </Choice>
                  )
                })}
              </Choices>
              <Field label={t('settings.theme.primary')}>
                <ColorRow>
                  <Picker type="color" value={fullHex(primary)} onChange={(e) => set({ primary: e.target.value })} />
                  <Input value={draft.primary} placeholder={effectivePrimary({ ...draft, primary: '' }, presets)} data-field="primary"
                    onChange={(e) => set({ primary: e.target.value })} style={{ width: 140 }} />
                  {draft.primary && <IconBtn type="button" title={t('settings.theme.usePalette')} onClick={() => set({ primary: '' })}><X size={14} /></IconBtn>}
                </ColorRow>
                <Hint>{draft.primary && !isHex(draft.primary) ? t('settings.theme.notHex') : t('settings.theme.primaryHint')}</Hint>
              </Field>
            </PartBody>
          </Part>

          <Part>
            <PartHead><TagIcon size={14} /> {t('settings.theme.appName')}<span className="state">{draft.appName.trim() || 'Liberty'}</span></PartHead>
            <PartBody>
              <Field label={t('settings.theme.appName')}>
                <Input value={draft.appName} placeholder="Liberty" data-field="app_name" onChange={(e) => set({ appName: e.target.value })} />
              </Field>
            </PartBody>
          </Part>

          <Part>
            <PartHead><Type size={14} /> {t('settings.theme.text')}<span className="state">{font?.label ?? ''}</span></PartHead>
            <PartBody>
              <Field label={t('settings.theme.font')}>
                <Choices>
                  {fontList.map((f) => (
                    <Choice key={f.id} type="button" $on={font?.id === f.id} data-font={f.id} onClick={() => set({ fontFamily: f.id })} style={{ fontFamily: f.stack }}>
                      <span className="aa">Aa</span><span className="name">{f.label}</span>
                    </Choice>
                  ))}
                </Choices>
                <Hint>{t('settings.theme.fontHint')}</Hint>
              </Field>
              <Field label={t('settings.theme.size.label')}>
                <Seg>
                  {SIZE_STEPS.map((s) => (
                    <SegBtn key={s.id} type="button" data-size={s.id} $on={Math.abs(draft.fontScale - s.value) < 1e-6} onClick={() => set({ fontScale: s.value })}>
                      {t(`settings.theme.size.${s.id}`)}
                    </SegBtn>
                  ))}
                </Seg>
              </Field>
            </PartBody>
          </Part>

          <Part>
            <PartHead $click onClick={() => setAdvanced((v) => !v)} data-part="advanced">
              {advanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {t('settings.theme.advanced')}
              <span className="state">{draft.vars.length ? t('settings.theme.varsCount', { n: draft.vars.length }) : t('settings.theme.none')}</span>
            </PartHead>
            {advanced && (
              <PartBody>
                <Hint>{t('settings.theme.advancedHint')}</Hint>
                {draft.vars.map(([name, value], i) => (
                  // (the lines have no identity but their place)
                  <VarLine key={i}>
                    <Input value={name} placeholder="bg-base" data-var="name" onChange={(e) => setVar(i, 0, e.target.value)} style={{ fontFamily: fonts.mono }} />
                    <Input value={value} placeholder="#101218" data-var="value" onChange={(e) => setVar(i, 1, e.target.value)} style={{ fontFamily: fonts.mono }} />
                    <IconBtn type="button" title={t('common.remove')} onClick={() => set({ vars: draft.vars.filter((_, j) => j !== i) })}><X size={14} /></IconBtn>
                  </VarLine>
                ))}
                <AddBtn type="button" onClick={() => set({ vars: [...draft.vars, ['', '']] })}><Plus size={13} /> {t('settings.theme.addVar')}</AddBtn>
              </PartBody>
            )}
          </Part>
        </Settings>

        <Beside>
          <Hint>{t('settings.theme.previewHint')}</Hint>
          <ThemePreview appName={draft.appName.trim()} vars={vars} />
        </Beside>
      </Split>
    </Shell>
  )
}
