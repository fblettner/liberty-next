// Design tokens — a macOS "liquid glass" palette, dark by default with a light
// variant toggled via the `.theme-light` class on <html>. Adapted from nomaubl's
// React app (../../JavaProjects/nomaubl/src/web-react/src/theme.ts) so Liberty's
// UI shares its look. Every colour resolves through a CSS var (see index.css) so
// the light theme is a pure class swap.

export const colors = {
  bg: {
    base: 'var(--bg-base, #07060e)',
    card: 'var(--bg-card, rgba(255,255,255,0.06))',
    input: 'var(--bg-input, rgba(255,255,255,0.04))',
    dropdown: 'var(--bg-dropdown, #1C2028)',
    modal: 'var(--bg-modal, rgba(30,32,42,0.82))',
  },

  border: 'var(--border, rgba(255,255,255,0.10))',

  text: {
    primary: 'var(--text-primary, #eef1ff)',
    secondary: 'var(--text-secondary, rgba(238,241,255,0.78))',
    muted: 'var(--text-muted, rgba(238,241,255,0.52))',
  },

  blue: {
    main: 'var(--blue-main, #007AFF)',
    bg: 'var(--blue-bg, rgba(0,122,255,0.15))',
    bgHover: 'var(--blue-bg-hover, rgba(0,122,255,0.25))',
    border: 'var(--blue-border, rgba(0,122,255,0.35))',
  },
  green: {
    main: 'var(--green-main, #32D74B)',
    bg: 'var(--green-bg, rgba(50,215,75,0.10))',
    border: 'var(--green-border, rgba(50,215,75,0.28))',
  },
  red: {
    main: 'var(--red-main, #FF453A)',
    bg: 'var(--red-bg, rgba(255,69,58,0.10))',
    border: 'var(--red-border, rgba(255,69,58,0.28))',
  },
  orange: {
    main: 'var(--orange-main, #FF9F0A)',
    bg: 'var(--orange-bg, rgba(255,159,10,0.10))',
    border: 'var(--orange-border, rgba(255,159,10,0.28))',
  },
  purple: {
    main: 'var(--purple-main, #BF5AF2)',
    bg: 'var(--purple-bg, rgba(191,90,242,0.10))',
    border: 'var(--purple-border, rgba(191,90,242,0.28))',
  },
  yellow: {
    main: 'var(--yellow-main, #FFD60A)',
    bg: 'var(--yellow-bg, rgba(255,214,10,0.10))',
    border: 'var(--yellow-border, rgba(255,214,10,0.28))',
  },
}

// The UI sans family reads a CSS var so the per-deployment Theme editor can re-skin it
// (--font-sans, set from theme.toml's font_family). The literal default is the fallback, so an
// un-branded install looks exactly as before. Code stays monospace (not operator-themed).
export const fonts = {
  sans: "var(--font-sans, 'DM Sans', -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', system-ui, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif)",
  mono: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace",
}

// Every size is calc(var(--font-scale, 1) * Npx) so the Theme editor's text-size control scales
// the whole UI proportionally (--font-scale from theme.toml's font_scale). Default scale 1 → the
// exact px values below. NOTE: these are CSS strings — use EDITOR_FONT_PX for numeric contexts
// (e.g. CodeMirror) that can't parse a calc().
const sz = (px: number) => `calc(var(--font-scale, 1) * ${px}px)`
export const fontSize = {
  micro: sz(10),
  sm: sz(11),
  base: sz(13),
  md: sz(14),
  lg: sz(15),
  xl: sz(16),
  '2xl': sz(18),
  '3xl': sz(20),
}

// Numeric base size for contexts that need a number, not a CSS string (CodeMirror's `fontSize`).
// Doesn't follow --font-scale (the code editor keeps a stable size).
export const EDITOR_FONT_PX = 13

export const radius = {
  sm: '6px',
  md: '10px',
  lg: '14px',
  xl: '18px',
}

export const shadow = {
  sm: 'var(--shadow-sm, 0 1px 4px rgba(0,0,0,0.2))',
  md: 'var(--shadow-md, 0 4px 16px rgba(0,0,0,0.32))',
  lg: 'var(--shadow-lg, 0 8px 24px rgba(0,0,0,0.4))',
  xl: 'var(--shadow-xl, 0 12px 40px rgba(0,0,0,0.45))',
  focus: 'var(--shadow-focus, 0 0 0 3px rgba(0,122,255,0.18))',
  modal: 'var(--shadow-modal, inset 0 1px 0 rgba(255,255,255,0.18), 0 12px 40px rgba(0,0,0,0.45))',
}

export const transition = 'all 0.15s ease'

export const glass = {
  surface: `
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
  `,
  input: `
    backdrop-filter: blur(10px) saturate(140%);
    -webkit-backdrop-filter: blur(10px) saturate(140%);
  `,
  specularSm: `
    box-shadow: var(--glass-specular, inset 0 1px 0 rgba(255,255,255,0.12), 0 4px 16px rgba(0,0,0,0.32));
  `,
}

export const media = {
  tablet: '@media (max-width: 768px)',
  mobile: '@media (max-width: 480px)',
}
