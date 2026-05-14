// A themed Monaco editor for SQL — the per-query SQL field in Settings' Connectors → Tables editor
// (Phase 7). Drops in where a `<Textarea rows={n}>` lived: same `value` / `onChange` shape, the
// `rows` prop maps to a pixel height so it slots in without re-laying-out the form. Pulls Monaco's
// SQL contribution (registered in services/monaco.ts) for keyword/builtins/comment colouring;
// theme syncs with the app's light/dark via `useIsLight()`. Heavier than the textarea it replaces,
// but it only lands on the Settings chunk (already where Monaco lives via RawEditor) — no entry-bundle
// regression.
//
// NOT re-exported from `common/index.ts` — consumers (SchemaForm's SqlField, the future test-runner)
// import this file directly, so the Monaco worker import stays inside the Settings chunk.
import '../services/monaco' // side effect: register Monaco + the SQL language; no-op when already loaded
import MonacoEditor, { type OnChange } from '@monaco-editor/react'
import styled from '@emotion/styled'
import { useIsLight } from './useIsLight'
import { Centered } from './Spinner'
import { colors, fontSize, fonts, radius } from '../theme'

const Frame = styled.div<{ $h: number }>`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;
  background: ${colors.bg.input}; height: ${({ $h }) => $h}px; min-height: 80px;
`

export interface SqlEditorProps {
  value: string
  onChange: (v: string) => void
  /** Pixel height per row, matched to `<Textarea rows={n}>`'s feel. Default ~6 rows. */
  rows?: number
  readOnly?: boolean
}

// One "row" is ~20px (Monaco's default line height at our font size) + 18px chrome for the gutter
// / scrollbar / borders. Matches a `<Textarea rows={6}>`'s visual weight; the textarea was 6 rows
// in the single-statement case and 4 rows per-dialect — same args, same look.
const ROW_PX = 20
const CHROME_PX = 18

export function SqlEditor({ value, onChange, rows = 6, readOnly }: SqlEditorProps) {
  const isLight = useIsLight()
  const handleChange: OnChange = (v) => onChange(v ?? '')
  return (
    <Frame $h={rows * ROW_PX + CHROME_PX}>
      <MonacoEditor
        height="100%"
        language="sql"
        theme={isLight ? 'light' : 'vs-dark'}
        value={value}
        loading={<Centered />}
        onChange={handleChange}
        options={{
          fontSize: parseInt(fontSize.base, 10),
          fontFamily: fonts.mono,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          tabSize: 2,
          renderWhitespace: 'boundary',
          wordWrap: 'on',
          automaticLayout: true,
          readOnly,
          // Tighten the gutter for the small per-field embed; full-screen RawEditor keeps the defaults.
          lineNumbersMinChars: 3,
          folding: false,
        }}
      />
    </Frame>
  )
}
