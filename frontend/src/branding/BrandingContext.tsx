// Runtime branding — applies the operator's per-deployment theme (GET /api/theme) on top of the
// static design tokens in ../theme.ts + index.css. The backend resolves the chosen preset /
// primary colour into a flat map of CSS custom properties (keys without the `--` prefix); we set
// them inline on <html>, which overrides the stylesheet defaults in both dark and light mode (the
// per-user dark/light toggle still works on top). Also surfaces the operator's `appName` so the
// login screen / tab strip / browser title show the brand instead of "Liberty Next".
//
// Public endpoint (no auth) so the sign-in page is branded too. The Theme editor (Settings) calls
// `preview` while editing and `refresh` after saving to re-pull + re-apply without a reload.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '../api/client'

export interface PresetChoice { id: string; label: string; primary?: string }

/** Derive the `blue-*` accent family from a primary hex — mirrors the server's `_primary_vars`
 *  (same alpha ratios) so the Theme editor can live-preview without a round-trip. The server
 *  re-derives authoritatively on save. */
export function derivePrimaryVars(primaryHex: string): Record<string, string> {
  const s = primaryHex.trim().replace(/^#/, '')
  const hex = s.length === 3 ? s.split('').map((c) => c + c).join('') : s
  if (!/^[0-9a-fA-F]{6}$/.test(hex)) return {}
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  const rgba = (a: number) => `rgba(${r},${g},${b},${a})`
  return {
    'blue-main': `#${hex}`,
    'blue-bg': rgba(0.15),
    'blue-bg-hover': rgba(0.25),
    'blue-border': rgba(0.35),
    'shadow-focus': `0 0 0 3px ${rgba(0.18)}`,
  }
}
export interface UiDefaults {
  /** The period preset a dashboard opens on when it names none (a user's remembered pick wins). */
  default_period: string
  /** The grid's initial page size. */
  default_page_size: number
  /** How long a dashboard widget's result is reused when the dashboard is reopened; 0 = always refetch. */
  dashboard_cache_seconds: number
  /** Several workspace tabs at once; off = one page at a time (opening another replaces it). */
  tabs_multi: boolean
  /** Close the open tabs when the operator switches application. */
  tabs_close_on_app_switch: boolean
  /** How many tabs at most; the oldest closes when the limit is reached. */
  tabs_max: number
}
export const UI_DEFAULTS: UiDefaults = { default_period: 'thisMonth', default_page_size: 50, dashboard_cache_seconds: 60, tabs_multi: true, tabs_close_on_app_switch: false, tabs_max: 5 }

export interface ResolvedTheme {
  preset: string
  app_name: string | null
  vars: Record<string, string>
  presets?: PresetChoice[]
  ui?: Partial<UiDefaults>
}

interface BrandingState {
  appName: string | null
  presets: PresetChoice[]
  /** App-level UI defaults ([app] in app.toml), with built-in fallbacks before the first fetch. */
  ui: UiDefaults
  /** Apply a flat var map to <html> immediately (live preview while editing). */
  applyVars: (vars: Record<string, string>) => void
  /** Re-fetch GET /api/theme and apply it (call after a save, or to discard a preview). */
  refresh: () => Promise<void>
}

const BrandingContext = createContext<BrandingState | null>(null)

// Track the keys we've set so a preview that drops a key can clear it (rather than leave a stale
// inline override shadowing the stylesheet). Module-level so applyVars stays referentially stable.
const applied = new Set<string>()
function setVars(vars: Record<string, string>): void {
  const root = document.documentElement
  // Clear any previously-applied key that's no longer present.
  for (const k of applied) {
    if (!(k in vars)) { root.style.removeProperty(`--${k}`); applied.delete(k) }
  }
  for (const [k, v] of Object.entries(vars)) {
    root.style.setProperty(`--${k}`, v)
    applied.add(k)
  }
}

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [appName, setAppName] = useState<string | null>(null)
  const [presets, setPresets] = useState<PresetChoice[]>([])
  const [ui, setUi] = useState<UiDefaults>(UI_DEFAULTS)
  const baseTitle = useRef<string>(document.title)

  const apply = useCallback((t: ResolvedTheme) => {
    setVars(t.vars || {})
    setAppName(t.app_name ?? null)
    if (t.presets) setPresets(t.presets)
    if (t.ui) setUi({ ...UI_DEFAULTS, ...t.ui })
    // Browser tab title — operator name if set, else whatever index.html shipped.
    document.title = t.app_name || baseTitle.current
  }, [])

  const refresh = useCallback(async () => {
    try {
      apply(await api.get<ResolvedTheme>('/api/theme'))
    } catch {
      /* branding is best-effort — a failed fetch just leaves the stylesheet defaults */
    }
  }, [apply])

  const applyVars = useCallback((vars: Record<string, string>) => setVars(vars), [])

  useEffect(() => { void refresh() }, [refresh])

  const value = useMemo<BrandingState>(
    () => ({ appName, presets, ui, applyVars, refresh }),
    [appName, presets, ui, applyVars, refresh],
  )
  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>
}

export function useBranding(): BrandingState {
  const ctx = useContext(BrandingContext)
  if (!ctx) throw new Error('useBranding must be used within <BrandingProvider>')
  return ctx
}
