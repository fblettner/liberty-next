// Settings → Theme — per-deployment branding (superuser). Pick a built-in preset, set the primary
// accent colour, and the app name; the change applies to the whole install (it's written to
// theme.toml on save and served by GET /api/theme to every client on load). Dark/light stays a
// per-user toggle in the top bar — this only re-skins the accent family + names the app.
//
// Live preview: as you change the preset/colour the accent is applied to the running UI via the
// BrandingContext (client-side derivation that mirrors the server). Save writes theme.toml (the
// server re-derives authoritatively) then re-pulls /api/theme; leaving without saving discards the
// preview (the cleanup re-applies the saved theme).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Save, RefreshCw, Palette } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Card, Field, Input, Button, Banner, Stack, Row, SpinnerRing } from '../../common'
import { useBranding, derivePrimaryVars, type PresetChoice } from '../../branding/BrandingContext'
import { colors, fontSize, fonts, radius } from '../../theme'

interface ThemeDoc { preset: string; app_name?: string | null; primary_color?: string | null; vars?: Record<string, string> }
interface ThemeParsed { path: string; theme: ThemeDoc; presets: PresetChoice[] }

const Grid = styled.div`display: grid; grid-template-columns: minmax(0, 360px) minmax(0, 1fr); gap: 18px; align-items: start;`
const Select = styled.select`
  height: 32px; padding: 0 8px; border-radius: ${radius.md}; border: 1px solid ${colors.border};
  background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.base};
`
const ColorRow = styled.div`display: flex; align-items: center; gap: 8px;`
const Swatch = styled.input`
  width: 38px; height: 32px; padding: 0; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: transparent; cursor: pointer;
`
const Preview = styled(Card)`padding: 16px; display: flex; flex-direction: column; gap: 12px;`
const PvBtns = styled.div`display: flex; gap: 8px; flex-wrap: wrap; align-items: center;`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`

export default function ThemeBuilder() {
  const { t } = useTranslation()
  const { applyVars, refresh } = useBranding()
  const [presets, setPresets] = useState<PresetChoice[]>([])
  const [preset, setPreset] = useState('default')
  const [appName, setAppName] = useState('')
  const [primary, setPrimary] = useState('')           // explicit override; '' → use the preset's primary
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setStatus(null)
    try {
      const r = await api.get<ThemeParsed>('/admin/config/theme/parsed')
      setPresets(r.presets)
      setPreset(r.theme.preset || 'default')
      setAppName(r.theme.app_name ?? '')
      setPrimary(r.theme.primary_color ?? '')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // The primary actually in effect: explicit override, else the chosen preset's primary.
  const presetPrimary = useMemo(
    () => presets.find((p) => p.id === preset)?.primary ?? '#007AFF',
    [presets, preset],
  )
  const effectivePrimary = (primary || presetPrimary).trim()

  // Live preview — apply the derived accent family to the running UI as the form changes.
  useEffect(() => {
    if (loading) return
    applyVars(derivePrimaryVars(effectivePrimary))
  }, [loading, effectivePrimary, applyVars])

  // Discard an unsaved preview on unmount (re-pull + re-apply the saved theme).
  useEffect(() => () => { void refresh() }, [refresh])

  const onPreset = (id: string) => { setPreset(id); setPrimary(''); setStatus(null) }   // preset reset clears the override

  const save = async () => {
    setBusy(true); setError(null); setStatus(null)
    const theme: ThemeDoc = { preset }
    if (appName.trim()) theme.app_name = appName.trim()
    if (primary.trim()) theme.primary_color = primary.trim()
    try {
      await api.put('/admin/config/theme/parsed', { theme })
      await refresh()                                   // re-pull the authoritative resolved theme
      setStatus(t('settings.theme.saved', 'Saved. The new theme is live for everyone.'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <SpinnerRing size={16} thickness={2} />

  return (
    <Stack gap={14}>
      <Hint>{t('settings.theme.intro', 'Brand the whole install — pick a preset palette, set the primary accent colour, and name the app. Applies to every user; dark/light stays an individual choice.')}</Hint>
      {error && <Banner $tone="error">{error}</Banner>}
      {status && <Banner $tone="ok">{status}</Banner>}
      <Grid>
        <Stack gap={12}>
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
          <Row gap={8}>
            <Button $variant="primary" $size="sm" onClick={() => void save()} disabled={busy}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
            </Button>
            <Button $variant="ghost" $size="sm" onClick={() => void load()} disabled={busy}>
              <RefreshCw size={13} /> {t('settings.theme.reset', 'Reset')}
            </Button>
          </Row>
        </Stack>

        <Preview>
          <Row gap={8} align="center"><Palette size={15} /> <strong style={{ fontFamily: fonts.sans, color: colors.text.primary }}>{appName || 'Liberty'}</strong></Row>
          <Hint>{t('settings.theme.previewHint', 'Live preview — buttons, links and active states use the primary accent.')}</Hint>
          <PvBtns>
            <Button $variant="primary" $size="sm">{t('common.save')}</Button>
            <Button $variant="ghost" $size="sm">{t('common.cancel', 'Cancel')}</Button>
            <a href="#" onClick={(e) => e.preventDefault()} style={{ color: colors.blue.main, fontFamily: fonts.sans, fontSize: fontSize.base }}>
              {t('settings.theme.sampleLink', 'A sample link')}
            </a>
          </PvBtns>
          <div style={{ height: 6, borderRadius: 3, background: colors.blue.main }} />
          <div style={{ height: 6, borderRadius: 3, background: colors.blue.bg, border: `1px solid ${colors.blue.border}` }} />
        </Preview>
      </Grid>
    </Stack>
  )
}
