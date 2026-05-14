// A themed Monaco editor for SQL — the per-query SQL field in Settings' Connectors → Tables editor
// (Phase 7). Drops in where a `<Textarea rows={n}>` lived: same `value` / `onChange` shape, the
// `rows` prop maps to a pixel height so it slots in without re-laying-out the form. Pulls Monaco's
// SQL contribution (registered in services/monaco.ts) for keyword/builtins/comment colouring;
// theme syncs with the app's light/dark via `useIsLight()`. Heavier than the textarea it replaces,
// but it only lands on the Settings chunk (already where Monaco lives via RawEditor) — no entry-bundle
// regression.
//
// With a `connector` prop set, the editor fetches that connector's pool schema (GET /api/sql/{c}/_schema)
// and wires schema-aware autocomplete — typing `FROM ` offers a list of tables, `<table>.` offers
// columns. The fetch is cached per-session (services/poolSchema.ts) so multiple editor instances on
// the same connector share one request.
//
// NOT re-exported from `common/index.ts` — consumers (SchemaForm's SqlField, the future test-runner)
// import this file directly, so the Monaco worker import stays inside the Settings chunk.
import '../services/monaco' // side effect: register Monaco + the SQL language; no-op when already loaded
import MonacoEditor, { type OnChange, type OnMount } from '@monaco-editor/react'
import styled from '@emotion/styled'
import { useState } from 'react'
import { Play, Wand2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useIsLight } from './useIsLight'
import { Centered } from './Spinner'
import { findFirstReferencedTable, getPoolSchema, type PoolSchema } from '../services/poolSchema'
import { attachPoolSchema } from '../services/sqlCompletion'
import { SqlWizardModal } from './SqlWizardModal'
import { SqlTestRunner } from './SqlTestRunner'
import { colors, fontSize, fonts, radius } from '../theme'

// `resize: vertical` lets the operator drag-resize the editor; Monaco's `automaticLayout: true`
// repaints as the box grows / shrinks. The default `$h` is the *initial* height — after the user
// drags it, the browser remembers the new height per-mount.
const Frame = styled.div<{ $h: number }>`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;
  background: ${colors.bg.input}; height: ${({ $h }) => $h}px; min-height: 120px; max-height: 80vh;
  resize: vertical;
`
const Toolbar = styled.div`
  display: flex; gap: 6px; justify-content: flex-end; margin-bottom: 4px;
`
const ToolBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 8px; border-radius: ${radius.sm};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  font-size: ${fontSize.micro}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
`

export interface SqlEditorProps {
  value: string
  onChange: (v: string) => void
  /** Pixel height per row, matched to `<Textarea rows={n}>`'s feel. Default ~6 rows. */
  rows?: number
  readOnly?: boolean
  /** When set, schema-aware autocomplete kicks in: tables after FROM/JOIN/INTO/UPDATE,
   *  columns after `<table>.`, plus the columns of any table referenced earlier in the
   *  statement when typing inside a SELECT clause. Also reveals the wizard + run buttons. */
  connector?: string
}

// One "row" is ~20px (Monaco's default line height at our font size) + 18px chrome for the gutter
// / scrollbar / borders. Matches a `<Textarea rows={6}>`'s visual weight; the textarea was 6 rows
// in the single-statement case and 4 rows per-dialect — same args, same look.
const ROW_PX = 20
const CHROME_PX = 18

export function SqlEditor({ value, onChange, rows = 6, readOnly, connector }: SqlEditorProps) {
  const { t } = useTranslation()
  const isLight = useIsLight()
  const [schema, setSchema] = useState<PoolSchema | null>(null)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [runnerOpen, setRunnerOpen] = useState(false)
  const handleChange: OnChange = (v) => onChange(v ?? '')
  const handleMount: OnMount = (editor, monaco) => {
    if (!connector) return
    const model = editor.getModel()
    if (!model) return
    // Schema fetch is async — by the time it resolves the editor may have been unmounted, but
    // attaching to a disposed model is harmless (the WeakMap entry just never gets read).
    void getPoolSchema(connector).then((s) => {
      if (s) {
        attachPoolSchema(monaco, model, s)
        setSchema(s)  // also enables the wizard button — needs the schema to populate the modal
      }
    })
  }
  // Toolbar shown only when this editor is bound to a connector (i.e. only in the config builder).
  // RawEditor / readOnly / no-connector cases keep the bare editor — matches what shipped before.
  const showToolbar = !!connector && !readOnly
  return (
    <div>
      {showToolbar && (
        <Toolbar>
          <ToolBtn type="button" disabled={!schema} onClick={() => setWizardOpen(true)}
            title={schema ? t('settings.sqlEditor.wizardTitle') : t('settings.sqlEditor.wizardLoading')}>
            <Wand2 size={11} /> {t('settings.sqlEditor.wizard')}
          </ToolBtn>
          <ToolBtn type="button" $active={runnerOpen} onClick={() => setRunnerOpen((v) => !v)}
            title={t('settings.sqlEditor.runTitle')}>
            <Play size={11} /> {t('settings.sqlEditor.run')}
          </ToolBtn>
        </Toolbar>
      )}
      <Frame $h={rows * ROW_PX + CHROME_PX}>
        <MonacoEditor
          height="100%"
          language="sql"
          theme={isLight ? 'light' : 'vs-dark'}
          value={value}
          loading={<Centered />}
          onChange={handleChange}
          onMount={handleMount}
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
            // Show the suggestion widget on Ctrl-Space (or auto on trigger chars); a small editor
            // doesn't need word-based suggestions polluting the schema list.
            quickSuggestions: { other: 'on', strings: false, comments: false },
            wordBasedSuggestions: 'off',
          }}
        />
      </Frame>
      {runnerOpen && connector && (
        <SqlTestRunner connector={connector} sql={value} onClose={() => setRunnerOpen(false)} />
      )}
      {wizardOpen && schema && (
        <SqlWizardModal schema={schema}
          // Best-effort seeding from the current SQL — the table being edited is usually the
          // first one mentioned after FROM/JOIN (works for v2's `SELECT * FROM (<orig>) lib_flt`
          // wrapper too — the inner real table is what matches the schema). Undefined → the
          // wizard falls back to its first table.
          initialTable={findFirstReferencedTable(value, schema)}
          onInsert={(sql) => { onChange(sql); setWizardOpen(false) }}
          onCancel={() => setWizardOpen(false)} />
      )}
    </div>
  )
}
