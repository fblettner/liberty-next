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
import type * as Monaco from 'monaco-editor/esm/vs/editor/editor.api'
import styled from '@emotion/styled'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Play, Wand2, Braces } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useIsLight } from './useIsLight'
import { Centered } from './Spinner'
import { findReferencedTables, fetchTablesSchema, getPoolSchemaNames } from '../services/poolSchema'
import { attachPoolSchema } from '../services/sqlCompletion'
import { SqlWizardModal, type WizardStatementType } from './SqlWizardModal'
import { SqlTestRunner } from './SqlTestRunner'
import { colors, fontSize, fonts, radius, EDITOR_FONT_PX } from '../theme'

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
const MenuWrap = styled.div`position: relative; margin-right: auto;`
const TokenMenu = styled.div`
  position: absolute; top: calc(100% + 4px); left: 0; z-index: 30; min-width: 230px;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  padding: 4px; box-shadow: 0 6px 24px rgba(0,0,0,0.28);
`
const TokenItem = styled.button`
  display: flex; flex-direction: column; gap: 1px; width: 100%; text-align: left; cursor: pointer;
  padding: 5px 8px; border: none; background: transparent; border-radius: ${radius.sm};
  & .tok { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .desc { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  &:hover { background: var(--hover-subtle); }
`
const TokenDivider = styled.div`
  font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em;
  padding: 6px 8px 2px; margin-top: 2px; border-top: 1px solid ${colors.border};
`
// The predefined query tokens the backend resolves at write time (liberty/connectors/sql.py:
// _PREDEFINED_TOKENS). Inserting one writes ``{{TOKEN}}`` at the cursor; it's bound + coerced to the
// assigned column's format on execute (SYSDATE → jdedate CYYDDD, SYSTIME → jdetime HHMMSS, …).
const QUERY_TOKENS = [
  { tok: 'LOGIN', descKey: 'tokLogin', descFallback: 'Current user (uppercased)' },
  { tok: 'PID', descKey: 'tokPid', descFallback: 'Program id — the connector name (e.g. ULPID)' },
  { tok: 'JOBN', descKey: 'tokJobn', descFallback: 'Job/workstation — "LIBERTY" (e.g. ULJOBN)' },
  { tok: 'JDEDATE', descKey: 'tokJdedate', descFallback: 'JDE Julian date today — CYYDDD integer (e.g. ULUPMJ)' },
  { tok: 'JDETIME', descKey: 'tokJdetime', descFallback: 'JDE time now — HHMMSS integer (e.g. ULUPMT)' },
  { tok: 'SYSDATE', descKey: 'tokSysdate', descFallback: 'Current date (native date column)' },
  { tok: 'SYSTIME', descKey: 'tokSystime', descFallback: 'Current time (native time column)' },
  { tok: 'SYSTIMESTAMP', descKey: 'tokSystimestamp', descFallback: 'Current date & time' },
] as const
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
  /** The kind of statement this slot holds (the CRUD tab: read→SELECT, update→UPDATE,
   *  insert→INSERT, delete→DELETE). Drives the wizard: it builds that kind and opens read-only
   *  when the current SQL's leading keyword doesn't match. Omitted → inferred from the SQL. */
  statementType?: WizardStatementType
}

// One "row" is ~20px (Monaco's default line height at our font size) + 18px chrome for the gutter
// / scrollbar / borders. Matches a `<Textarea rows={6}>`'s visual weight; the textarea was 6 rows
// in the single-statement case and 4 rows per-dialect — same args, same look.
const ROW_PX = 20
const CHROME_PX = 18

export function SqlEditor({ value, onChange, rows = 6, readOnly, connector, statementType }: SqlEditorProps) {
  const { t } = useTranslation()
  const isLight = useIsLight()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [runnerOpen, setRunnerOpen] = useState(false)
  const [tokenOpen, setTokenOpen] = useState(false)
  const tokenWrapRef = useRef<HTMLDivElement>(null)
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)
  const monacoRef = useRef<typeof Monaco | null>(null)
  const handleChange: OnChange = (v) => onChange(v ?? '')
  // Insert a snippet at the cursor (replacing any selection), then refocus. Monaco's edit fires
  // onChange, so the controlled `value` stays in sync. Falls back to appending if the editor hasn't
  // mounted yet. ``insertToken`` wraps a predefined ``{{TOKEN}}``; the schema picker inserts the raw
  // ``#SCHEMA.<KEY>#`` placeholder.
  const insertSnippet = (snippet: string) => {
    const ed = editorRef.current
    setTokenOpen(false)
    if (ed) {
      const sel = ed.getSelection()
      if (sel) {
        ed.executeEdits('insert-token', [{ range: sel, text: snippet, forceMoveMarkers: true }])
        ed.focus()
        return
      }
    }
    onChange(value + snippet)
  }
  const insertToken = (tok: string) => insertSnippet(`{{${tok}}}`)
  // The connector pool's ``#SCHEMA.<KEY>#`` map (e.g. CTL → PS920CTL) — offered in the Token menu so
  // the operator picks the portable placeholder instead of typing it. Empty for pools without a map.
  const [schemaTokens, setSchemaTokens] = useState<{ key: string; owner: string }[]>([])
  useEffect(() => {
    if (!connector) { setSchemaTokens([]); return }
    let cancelled = false
    void getPoolSchemaNames(connector).then((s) => {
      if (cancelled) return
      const map = s?.schema_map ?? {}
      setSchemaTokens(Object.entries(map).map(([key, owner]) => ({ key, owner: String(owner) })))
    })
    return () => { cancelled = true }
  }, [connector])
  // Close the token menu on an outside click.
  useEffect(() => {
    if (!tokenOpen) return
    const h = (e: MouseEvent) => { if (tokenWrapRef.current && !tokenWrapRef.current.contains(e.target as Node)) setTokenOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [tokenOpen])
  const handleMount: OnMount = (editor, monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco
  }
  // Stable key of the referenced-table SET (qualified names) — only changes when the operator adds
  // / removes / finishes-typing a FROM-or-JOIN table, NOT on every keystroke inside a SELECT list.
  const refKey = useMemo(() => {
    if (!connector) return ''
    return findReferencedTables(value).map((r) => `${r.schema ?? ''}.${r.name}`).join('|').toLowerCase()
  }, [connector, value])
  // Fetch ONLY the referenced tables' columns (debounced) and (re)attach them for autocomplete.
  // Targeted ``name_like`` fetches resolve the 1-3 tables a query touches in ms — no full-pool walk.
  useEffect(() => {
    if (!connector) return
    let cancelled = false
    const refs = findReferencedTables(value)
    const handle = setTimeout(() => {
      void fetchTablesSchema(connector, refs).then((s) => {
        if (cancelled || !s) return
        const model = editorRef.current?.getModel()
        if (model && monacoRef.current) attachPoolSchema(monacoRef.current, model, s)
      })
    }, 300)
    return () => { cancelled = true; clearTimeout(handle) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refKey is the stable serialization of the referenced-table set
  }, [connector, refKey])
  // Toolbar shown whenever the field is editable — the Token inserter works on any SQL field
  // (with or without a connector). The wizard / run buttons still require a connector (schema +
  // execution). A read-only / RawEditor mount keeps the bare editor — matches what shipped before.
  const showToolbar = !readOnly
  // Never offer Run on a write statement (INSERT / UPDATE / DELETE / …) — executing it (even a
  // dry-run) against the live DB is dangerous. Only read queries (SELECT / CTE-SELECT / WITH …)
  // get the runner. Cheap leading-keyword check on the SQL.
  const isWriteSql = /^\s*(insert|update|delete|merge|truncate|create|drop|alter|replace|upsert|call)\b/i.test(value)
  const canRun = !isWriteSql
  return (
    <div>
      {showToolbar && (
        <Toolbar>
          <MenuWrap ref={tokenWrapRef}>
            <ToolBtn type="button" $active={tokenOpen} onClick={() => setTokenOpen((v) => !v)}
              title={t('settings.sqlEditor.tokenTitle', 'Insert a predefined value resolved at write time')}>
              <Braces size={11} /> {t('settings.sqlEditor.token', 'Token')}
            </ToolBtn>
            {tokenOpen && (
              <TokenMenu>
                {QUERY_TOKENS.map((q) => (
                  <TokenItem key={q.tok} type="button" onClick={() => insertToken(q.tok)}>
                    <span className="tok">{`{{${q.tok}}}`}</span>
                    <span className="desc">{t(`settings.sqlEditor.${q.descKey}`, q.descFallback)}</span>
                  </TokenItem>
                ))}
                {schemaTokens.length > 0 && (
                  <>
                    <TokenDivider>{t('settings.sqlEditor.schemaTokens', 'Pool schemas')}</TokenDivider>
                    {schemaTokens.map((s) => (
                      <TokenItem key={s.key} type="button" onClick={() => insertSnippet(`#SCHEMA.${s.key}#`)}>
                        <span className="tok">{`#SCHEMA.${s.key}#`}</span>
                        <span className="desc">{s.owner}</span>
                      </TokenItem>
                    ))}
                  </>
                )}
              </TokenMenu>
            )}
          </MenuWrap>
          {connector && (
            <ToolBtn type="button" onClick={() => setWizardOpen(true)}
              title={t('settings.sqlEditor.wizardTitle')}>
              <Wand2 size={11} /> {t('settings.sqlEditor.wizard')}
            </ToolBtn>
          )}
          {connector && canRun && (
            <ToolBtn type="button" $active={runnerOpen} onClick={() => setRunnerOpen((v) => !v)}
              title={t('settings.sqlEditor.runTitle')}>
              <Play size={11} /> {t('settings.sqlEditor.run')}
            </ToolBtn>
          )}
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
            fontSize: EDITOR_FONT_PX,
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
      {runnerOpen && connector && canRun && (
        <SqlTestRunner connector={connector} sql={value} onClose={() => setRunnerOpen(false)} />
      )}
      {wizardOpen && connector && (
        <SqlWizardModal
          connector={connector}
          // The full existing SQL — the wizard lazily loads each referenced table's columns and
          // tries to parse the simple single-table SELECT shape so opening on an existing query
          // pre-fills its widgets instead of replacing a working query with a regenerated default.
          initialSql={value}
          statementType={statementType}
          onInsert={(sql) => { onChange(sql); setWizardOpen(false) }}
          onCancel={() => setWizardOpen(false)} />
      )}
    </div>
  )
}
