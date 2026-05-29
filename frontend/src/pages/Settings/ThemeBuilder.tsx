// Settings → Theme — per-deployment branding (superuser). Pick a built-in preset, set the primary
// accent colour, name the app, choose a font + text size. The change applies to the whole install
// (written to theme.toml on save and served by GET /api/theme to every client on load). Dark/light
// stays a per-user toggle in the top bar — this only re-skins the accent family + names the app.
//
// Layout: same shape every other editor uses — a top toolbar (Save / Discard), then a clear
// two-card split underneath (Settings on the left, Live preview on the right). No more "is this
// the settings or the preview?" confusion.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Save, Undo2, Palette } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Field, Input, Row, SpinnerRing, Stack } from '../../common'
import { useBranding, derivePrimaryVars, type PresetChoice } from '../../branding/BrandingContext'
import { colors, fontSize, fonts, radius } from '../../theme'

interface ThemeDoc { preset: string; app_name?: string | null; primary_color?: string | null; font_family?: string | null; font_scale?: number | null; vars?: Record<string, string> }
interface FontChoice { id: string; label: string; stack: string }
interface ThemeParsed { path: string; theme: ThemeDoc; presets: PresetChoice[]; fonts: FontChoice[] }

const SIZE_STEPS = [
  { id: 'compact', value: 0.9 },
  { id: 'normal', value: 1.0 },
  { id: 'comfortable', value: 1.1 },
  { id: 'large', value: 1.2 },
] as const

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const Grid = styled.div`display: grid; grid-template-columns: minmax(0, 420px) minmax(0, 1fr); gap: 14px; align-items: start; flex-shrink: 0;`
const Panel = styled(Card)`padding: 0; display: flex; flex-direction: column; overflow: hidden;`
const PanelHeader = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 12px 14px;
  border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600;
  color: ${colors.text.primary};
  text-transform: uppercase; letter-spacing: 0.06em;
  & svg { color: ${colors.blue.main}; }
`
const PanelBody = styled.div`padding: 14px; display: flex; flex-direction: column; gap: 12px;`
const Select = styled.select`
  height: 32px; padding: 0 8px; border-radius: ${radius.md}; border: 1px solid ${colors.border};
  background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.base};
`
const ColorRow = styled.div`display: flex; align-items: center; gap: 8px;`
const Swatch = styled.input`
  width: 38px; height: 32px; padding: 0; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: transparent; cursor: pointer;
`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean }>`
  height: 32px; padding: 0 12px; border: none; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.base};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
  &:hover { color: ${colors.text.primary}; }
`
const PreviewBlock = styled.div`display: flex; flex-direction: column; gap: 14px;`
const SwatchTile = styled.div<{ $bg: string; $border?: string }>`
  height: 32px; border-radius: ${radius.md}; background: ${({ $bg }) => $bg};
  ${({ $border }) => $border ? `border: 1px solid ${$border};` : ''}
`
const PvLink = styled.a`color: ${colors.blue.main}; font-family: ${fonts.sans}; font-size: ${fontSize.base};`
const AppNamePreview = styled.strong`font-family: ${fonts.sans}; font-size: ${fontSize.lg}; color: ${colors.text.primary};`

export default function ThemeBuilder() {
  const { t } = useTranslation()
  const { applyVars, refresh } = useBranding()
  const [presets, setPresets] = useState<PresetChoice[]>([])
  const [fonts_, setFonts] = useState<FontChoice[]>([])
  const [original, setOriginal] = useState('')
  const [preset, setPreset] = useState('default')
  const [appName, setAppName] = useState('')
  const [primary, setPrimary] = useState('')
  const [fontFamily, setFontFamily] = useState('')
  const [fontScale, setFontScale] = useState(1)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setStatus(null)
    try {
      const r = await api.get<ThemeParsed>('/admin/config/theme/parsed')
      setPresets(r.presets)
      setFonts(r.fonts ?? [])
      const seed = {
        preset: r.theme.preset || 'default',
        appName: r.theme.app_name ?? '',
        primary: r.theme.primary_color ?? '',
        fontFamily: r.theme.font_family ?? '',
        fontScale: r.theme.font_scale ?? 1,
      }
      setPreset(seed.preset); setAppName(seed.appName); setPrimary(seed.primary)
      setFontFamily(seed.fontFamily); setFontScale(seed.fontScale)
      setOriginal(JSON.stringify(seed))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const dirty = useMemo(
    () => JSON.stringify({ preset, appName, primary, fontFamily, fontScale }) !== original,
    [preset, appName, primary, fontFamily, fontScale, original],
  )

  const presetPrimary = useMemo(
    () => presets.find((p) => p.id === preset)?.primary ?? '#007AFF',
    [presets, preset],
  )
  const effectivePrimary = (primary || presetPrimary).trim()
  const fontStack = useMemo(() => fonts_.find((f) => f.id === fontFamily)?.stack ?? '', [fonts_, fontFamily])

  useEffect(() => {
    if (loading) return
    const vars: Record<string, string> = { ...derivePrimaryVars(effectivePrimary) }
    if (fontStack) vars['font-sans'] = fontStack
    if (Math.abs(fontScale - 1) > 1e-6) vars['font-scale'] = String(fontScale)
    applyVars(vars)
  }, [loading, effectivePrimary, fontStack, fontScale, applyVars])

  useEffect(() => () => { void refresh() }, [refresh])

  const onPreset = (id: string) => { setPreset(id); setPrimary(''); setStatus(null) }

  const discard = () => {
    if (!original) return
    try {
      const seed = JSON.parse(original) as { preset: string; appName: string; primary: string; fontFamily: string; fontScale: number }
      setPreset(seed.preset); setAppName(seed.appName); setPrimary(seed.primary)
      setFontFamily(seed.fontFamily); setFontScale(seed.fontScale)
      setStatus(null); setError(null)
    } catch { /* ignore */ }
  }

  const save = async () => {
    setBusy(true); setError(null); setStatus(null)
    const theme: ThemeDoc = { preset }
    if (appName.trim()) theme.app_name = appName.trim()
    if (primary.trim()) theme.primary_color = primary.trim()
    if (fontFamily) theme.font_family = fontFamily
    if (Math.abs(fontScale - 1) > 1e-6) theme.font_scale = fontScale
    try {
      await api.put('/admin/config/theme/parsed', { theme })
      await refresh()
      setStatus(t('settings.theme.saved', 'Saved. The new theme is live for everyone.'))
      setOriginal(JSON.stringify({ preset, appName, primary, fontFamily, fontScale }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (loading) return <SpinnerRing size={16} thickness={2} />

  return (
    <Shell>
      <Toolbar>
        <ToolbarLeft>
          {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
          {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
          {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        </ToolbarLeft>
        <ToolbarRight>
          <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty}>
            <Undo2 size={13} /> {t('common.discard', 'Discard')}
          </Button>
          <Button $variant="primary" $size="sm" onClick={() => void save()} disabled={busy || !dirty}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
          </Button>
        </ToolbarRight>
      </Toolbar>

      <Hint>{t('settings.theme.intro', 'Brand the whole install — pick a preset palette, set the primary accent colour, and name the app. Applies to every user; dark/light stays an individual choice.')}</Hint>
      {error && <Banner $tone="error">{error}</Banner>}

      <Grid>
        <Panel>
          <PanelHeader><Palette size={14} /> {t('settings.theme.settings', 'Settings')}</PanelHeader>
          <PanelBody>
            <Field label={t('settings.theme.preset', 'Preset')}>
              <Select value={preset} onChange={(e) => onPreset(e.target.value)}>
                {presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </Select>
            </Field>
            <Field label={t('settings.theme.primary', 'Primary colour')}>
              <ColorRow>
                <Swatch type="color" value={/^#[0-9a-fA-F]{6}$/.test(effectivePrimary) ? effectivePrimary : '#007AFF'}
                  onChange={(e) => { setPrimary(e.target.value); setStatus(null) }} />
                <Input value={primary} placeholder={presetPrimary}
                  onChange={(e) => { setPrimary(e.target.value); setStatus(null) }} style={{ width: 140 }} />
              </ColorRow>
              <Hint>{t('settings.theme.primaryHint', "Leave blank to use the preset's colour.")}</Hint>
            </Field>
            <Field label={t('settings.theme.appName', 'App name')}>
              <Input value={appName} placeholder="Liberty" onChange={(e) => { setAppName(e.target.value); setStatus(null) }} />
            </Field>
            <Field label={t('settings.theme.font', 'Font family')}>
              <Select value={fontFamily || (fonts_[0]?.id ?? '')} onChange={(e) => { setFontFamily(e.target.value); setStatus(null) }}>
                {fonts_.map((f) => <option key={f.id} value={f.id} style={{ fontFamily: f.stack }}>{f.label}</option>)}
              </Select>
              <Hint>{t('settings.theme.fontHint', 'The UI font for the whole install. Code stays monospace.')}</Hint>
            </Field>
            <Field label={t('settings.theme.size', 'Text size')}>
              <Seg>
                {SIZE_STEPS.map((s) => (
                  <SegBtn key={s.id} type="button" $active={Math.abs(fontScale - s.value) < 1e-6}
                    onClick={() => { setFontScale(s.value); setStatus(null) }}>
                    {t(`settings.theme.size${s.id.charAt(0).toUpperCase()}${s.id.slice(1)}`, s.id)}
                  </SegBtn>
                ))}
              </Seg>
            </Field>
          </PanelBody>
        </Panel>

        <Panel>
          <PanelHeader><Palette size={14} /> {t('settings.theme.preview', 'Preview')}</PanelHeader>
          <PanelBody>
            <Row gap={10} align="center">
              <AppNamePreview>{appName || 'Liberty'}</AppNamePreview>
            </Row>
            <Hint>{t('settings.theme.previewHint', 'Live preview — buttons, links and active states use the primary accent.')}</Hint>
            <PreviewBlock>
              <Stack gap={10}>
                <Row gap={8} align="center">
                  <Button $variant="primary" $size="sm">{t('common.save')}</Button>
                  <Button $variant="ghost" $size="sm">{t('common.cancel', 'Cancel')}</Button>
                  <PvLink href="#" onClick={(e) => e.preventDefault()}>
                    {t('settings.theme.sampleLink', 'A sample link')}
                  </PvLink>
                </Row>
                <SwatchTile $bg={colors.blue.main} />
                <SwatchTile $bg={colors.blue.bg} $border={colors.blue.border} />
              </Stack>
            </PreviewBlock>
          </PanelBody>
        </Panel>
      </Grid>
    </Shell>
  )
}
