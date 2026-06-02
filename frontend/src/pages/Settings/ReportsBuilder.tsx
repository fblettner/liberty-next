// Settings → Reports — PDF branding defaults applied to every report rendered
// through the framework (liberty.reports.render.render_content). Edits the
// `[reports.branding]` section of config/app.toml via GET/PUT
// /admin/config/reports/branding. Live-applies on save — next /api/reports/.../run
// picks the new values up without a server restart (the pipeline reads them
// off app.state.settings.reports.branding fresh).
//
// Layered precedence at render time:
//   framework defaults  <  [reports.branding] (this page)  <  per-report pdf_options
// So a plugin can still pick its own colour / eyebrow for a specific report,
// but anything it doesn't set inherits from here instead of the framework's
// generic "Document" / "Rapport" placeholder.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Save, Undo2, RefreshCw, FileText } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Field, Input, SpinnerRing, Tag } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

interface BrandingFields {
  author: string
  primary_color: string
  primary_color_light: string
  cover_eyebrow: string
  cover_ref: string
  footer_left: string
}

interface BrandingResponse {
  path: string
  branding: BrandingFields
  defaults: BrandingFields
}

const Shell = styled.div`display: flex; flex-direction: column; gap: 14px; flex: 1; min-height: 0;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const Path = styled.code`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  color: ${colors.text.muted}; padding: 2px 6px; border-radius: ${radius.sm};
  background: ${colors.bg.input};
`
const Grid = styled.div`
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px 18px;
  @media (max-width: 700px) { grid-template-columns: 1fr; }
`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const ColorRow = styled.div`display: flex; align-items: center; gap: 8px;`
const ColorSwatch = styled.span<{ $color: string }>`
  width: 28px; height: 28px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border};
  background: ${({ $color }) => $color || 'transparent'};
  flex-shrink: 0;
`

export default function ReportsBuilder() {
  const { t } = useTranslation()
  const [path, setPath] = useState('')
  const [branding, setBranding] = useState<BrandingFields | null>(null)
  const [defaults, setDefaults] = useState<BrandingFields | null>(null)
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setStatus(null)
    try {
      const r = await api.get<BrandingResponse>('/admin/config/reports/branding')
      setPath(r.path)
      setBranding(r.branding)
      setDefaults(r.defaults)
      setOriginal(JSON.stringify(r.branding))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const dirty = useMemo(
    () => branding !== null && JSON.stringify(branding) !== original,
    [branding, original],
  )

  const discard = () => {
    if (!original) return
    try {
      setBranding(JSON.parse(original) as BrandingFields)
      setStatus(null); setError(null)
    } catch { /* ignore */ }
  }

  const resetToDefaults = () => {
    if (!defaults) return
    setBranding({ ...defaults })
    setStatus(null); setError(null)
  }

  const save = async () => {
    if (!branding) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean; path: string }>(
        '/admin/config/reports/branding',
        { branding },
      )
      setOriginal(JSON.stringify(branding))
      setStatus(t('settings.reports.saved'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (loading || !branding) return <SpinnerRing size={16} thickness={2} />

  const update = (k: keyof BrandingFields, v: string) => {
    setBranding((b) => (b ? { ...b, [k]: v } : b))
  }

  return (
    <Shell>
      <Toolbar>
        <ToolbarLeft>
          <FileText size={16} />
          <strong>{t('settings.reports.title')}</strong>
          <Path>{path}</Path>
          {dirty && <Tag $tone="orange">{t('settings.reports.unsaved')}</Tag>}
        </ToolbarLeft>
        <Button $size="sm" $variant="ghost" onClick={resetToDefaults} disabled={busy}>
          <RefreshCw size={14} /> {t('settings.reports.resetDefaults')}
        </Button>
        <Button $size="sm" $variant="ghost" onClick={discard} disabled={!dirty || busy}>
          <Undo2 size={14} /> {t('common.discard')}
        </Button>
        <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={!dirty || busy}>
          <Save size={14} /> {busy ? t('common.working') : t('common.save')}
        </Button>
      </Toolbar>

      <Hint>{t('settings.reports.description')}</Hint>

      {error && <Banner $tone="error">{error}</Banner>}
      {status && <Banner $tone="ok">{status}</Banner>}

      <Grid>
        <Field label={t('settings.reports.fields.author')} htmlFor="branding-author">
          <Input
            id="branding-author"
            type="text"
            value={branding.author}
            onChange={(e) => update('author', e.target.value)}
            placeholder={defaults?.author || 'NOMANA-IT'}
          />
        </Field>

        <Field label={t('settings.reports.fields.coverEyebrow')} htmlFor="branding-eyebrow">
          <Input
            id="branding-eyebrow"
            type="text"
            value={branding.cover_eyebrow}
            onChange={(e) => update('cover_eyebrow', e.target.value)}
            placeholder={defaults?.cover_eyebrow}
          />
        </Field>

        <Field label={t('settings.reports.fields.primaryColor')} htmlFor="branding-color">
          <ColorRow>
            <Input
              id="branding-color"
              type="text"
              value={branding.primary_color}
              onChange={(e) => update('primary_color', e.target.value)}
              placeholder={defaults?.primary_color}
              style={{ flex: 1, fontFamily: 'monospace' }}
            />
            <ColorSwatch $color={branding.primary_color} />
          </ColorRow>
        </Field>

        <Field label={t('settings.reports.fields.primaryColorLight')} htmlFor="branding-color-light">
          <ColorRow>
            <Input
              id="branding-color-light"
              type="text"
              value={branding.primary_color_light}
              onChange={(e) => update('primary_color_light', e.target.value)}
              placeholder={defaults?.primary_color_light}
              style={{ flex: 1, fontFamily: 'monospace' }}
            />
            <ColorSwatch $color={branding.primary_color_light} />
          </ColorRow>
        </Field>

        <Field label={t('settings.reports.fields.coverRef')} htmlFor="branding-cover-ref">
          <Input
            id="branding-cover-ref"
            type="text"
            value={branding.cover_ref}
            onChange={(e) => update('cover_ref', e.target.value)}
            placeholder={defaults?.cover_ref}
          />
        </Field>

        <Field label={t('settings.reports.fields.footerLeft')} htmlFor="branding-footer">
          <Input
            id="branding-footer"
            type="text"
            value={branding.footer_left}
            onChange={(e) => update('footer_left', e.target.value)}
            placeholder={defaults?.footer_left}
          />
        </Field>
      </Grid>
    </Shell>
  )
}
