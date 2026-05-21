// Dedicated editor for ``Screen.export`` — replaces the auto-generated SchemaForm with a
// task-shaped UI:
//
// * Workbook-level: ``split_by`` is a **SearchSelect over the read query's columns** (no
//   typo'ed column names), ``file_name_template`` / ``archive_name`` are text inputs with
//   a **click-to-insert placeholder chip strip** below them ({{screen}}, {{split_value}}).
// * Sheet-level: ``connector`` is a SearchSelect over SQL connectors, ``query`` is a
//   SearchSelect over the picked connector's queries (with an Edit pencil that raises
//   EditQueryModal). ``split_by`` is free text (sheet result columns aren't catalogued —
//   they're discovered at execute time). ``name`` has the same placeholder chip strip with
//   ``{{sheet_value}}`` added when the sheet's ``split_by`` is set. ``param_binds`` uses
//   the shared ``ParamBindList`` component so the source autocomplete includes
//   ``split_value`` and the param names come from the picked query's metadata.

import { useMemo, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Edit3, Plus, Trash2 } from 'lucide-react'
import {
  Button, Field, Input, Row, SearchSelect, Stack, Tag, useModals,
  type JsonSchema, type SearchSelectOption,
} from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import { EditQueryModal } from './EditQueryModal'
import ParamBindList, { type ParamBind } from './ParamBindList'
import type { Column, SqlQueryMeta } from '../../types/connectors'

type Row = Record<string, unknown>

interface ExportEditorProps {
  /** The current ``Screen.export`` value (or null when not configured). */
  value: Row | null
  /** Replace the whole ``Screen.export`` value (null to delete the config). */
  onChange: (next: Row | null) => void
  /** The Screen's effective connector — used to default the sheet's connector + the
   *  workbook's ``split_by`` column dropdown. */
  effectiveConnector: string
  /** The Screen's ``read_query`` — drives the workbook's ``split_by`` column list (the
   *  workbook-level split walks the read query's distinct values). */
  readQuery: string
  /** The Screen's ``columns`` (resolved ColumnHints from the read query). The
   *  workbook-level ``split_by`` SearchSelect pulls from this. Empty list → fallback to
   *  free-text + a hint. */
  columns: Column[] | undefined
  /** The full ``$defs`` map from ``GET /admin/config/schema``. Currently unused (the
   *  ParamBindList renders the binds itself), kept on the API surface so a future drill
   *  can re-introduce SchemaForm sub-views without changing the call site. */
  defs: Record<string, JsonSchema>
  /** Called after EditQueryModal saves a query (so the parent can refresh the workspace's
   *  connectors metadata if needed). Optional. */
  onQueryEdited?: () => void
}

// Small inline strip of clickable placeholder chips. Clicking a chip appends the placeholder
// token to the current value (most common use: the operator types a prefix, then taps the chip
// to insert the dynamic part). Keeping this simple — no caret-position tracking — because the
// templates are short (one or two tokens) and the chip-strip is meant as a discovery aid, not
// a full template editor.
const ChipStrip = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 4px;
`

const Chip = styled.button`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: ${radius.sm};
  font-family: ${fonts.mono};
  font-size: ${fontSize.sm};
  background: ${colors.bg.input};
  border: 1px solid ${colors.border};
  color: ${colors.text.muted};
  cursor: pointer;
  &:hover {
    border-color: ${colors.blue.border};
    color: ${colors.blue.main};
  }
`

const SheetCard = styled.div`
  border: 1px solid ${colors.border};
  border-radius: ${radius.md};
  padding: 12px;
  background: ${colors.bg.card};
`

const SheetHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
`

const SheetTitle = styled.div`
  font-weight: 600;
  font-size: ${fontSize.md};
  color: ${colors.text.primary};
`

const Sub = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  line-height: 1.5;
`

const Hint = styled.div`
  font-size: ${fontSize.micro};
  color: ${colors.text.muted};
  margin-top: 3px;
  line-height: 1.5;
`

const ChipLabel = styled.span`
  font-size: ${fontSize.micro};
  color: ${colors.text.muted};
  margin-right: 2px;
`

/** Append a placeholder token to the field's current value. Trimmed-end + space before
 *  the chip so consecutive clicks don't smush together. */
function appendToken(current: string | null | undefined, token: string): string {
  const cur = (current ?? '').trimEnd()
  if (!cur) return token
  return `${cur} ${token}`
}

export default function ExportEditor({
  value, onChange, effectiveConnector, readQuery, columns, defs: _defs, onQueryEdited,
}: ExportEditorProps): ReactNode {
  const { t } = useTranslation()
  const { connectors: wsConnectors } = useWorkspace()
  const modals = useModals()

  // Imperative-from-async editing of a query — same shape ScreenVisualBuilder uses for its
  // nested-form tab query pickers. Triggered by the pencil button next to the sheet's query
  // SearchSelect. EditQueryModal handles its own save + reload; we just bump ``onQueryEdited``
  // so the parent can refresh anything that depends on the connector metadata.
  const [editQuery, setEditQuery] = useState<{ connector: string; queryName: string } | null>(null)

  // ── Workbook-level ────────────────────────────────────────────────────────────────────────
  const setProp = (key: string, next: unknown) => {
    onChange({ ...(value ?? {}), [key]: next })
  }
  const splitBy = (value?.split_by as string | null | undefined) ?? ''
  const fileNameTemplate = (value?.file_name_template as string | null | undefined) ?? ''
  const archiveName = (value?.archive_name as string | null | undefined) ?? ''
  const sheets = useMemo<Row[]>(() => Array.isArray(value?.sheets) ? (value!.sheets as Row[]) : [], [value])

  // The screen's read query columns drive the workbook-level split_by picker. We use
  // ``allowCustom`` so an operator with empty hints can still type a column name (the runtime
  // matches case-insensitively against discovered columns).
  const readColumnOptions = useMemo<SearchSelectOption[]>(() => {
    return (columns ?? []).map((c) => ({
      value: c.name,
      label: c.label || c.name,
      mono: c.name,
    }))
  }, [columns])

  // SQL connectors only — API connectors don't fit the sheet's ``query`` shape.
  const sqlConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? [])
      .filter((c) => c.type === 'sql')
      .map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )

  // Per-sheet effective connector → the sheet's explicit ``connector`` field, else the screen's.
  function sheetEffectiveConnector(sheet: Row): string {
    const c = (sheet.connector as string | null | undefined) ?? ''
    return c.trim() || effectiveConnector
  }

  // Available queries for a given (effective) connector. SQL queries only.
  function queryOptionsFor(connectorName: string): SearchSelectOption[] {
    const meta = (wsConnectors ?? []).find((c) => c.name === connectorName)
    if (!meta || meta.type !== 'sql') return []
    return meta.queries.map((q) => ({
      value: q.name,
      label: q.description || q.label || q.name,
      mono: q.name,
    }))
  }
  // Look up a specific query's metadata so we can surface its declared params + scanned
  // bind_params as the ``param`` autocomplete for ParamBindList.
  function queryMetaFor(connectorName: string, queryName: string): SqlQueryMeta | null {
    if (!queryName) return null
    const meta = (wsConnectors ?? []).find((c) => c.name === connectorName)
    if (!meta || meta.type !== 'sql') return null
    return meta.queries.find((q) => q.name === queryName) ?? null
  }

  // ── Sheet mutators ───────────────────────────────────────────────────────────────────────
  const patchSheet = (idx: number, patch: Row) => {
    const next = sheets.slice()
    next[idx] = { ...next[idx], ...patch }
    setProp('sheets', next)
  }
  const removeSheet = async (idx: number) => {
    const ok = await modals.confirm({
      title: t('settings.screens.export.removeSheetTitle', 'Remove sheet?'),
      message: t('settings.screens.export.removeSheetMsg', 'The sheet will be removed from the export. The query is not affected.'),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const next = sheets.slice()
    next.splice(idx, 1)
    setProp('sheets', next)
  }
  const addSheet = () => {
    const next: Row = {
      name: '',
      query: '',
      param_binds: [],
    }
    setProp('sheets', [...sheets, next])
  }

  // ── Render ────────────────────────────────────────────────────────────────────────────────
  // Workbook-level placeholder chips: every field supports {{screen}}; the two file-name
  // fields also get {{split_value}}. Each chip appends its token to the field on click.
  const renderWorkbookChips = (field: 'file_name_template' | 'archive_name'): ReactNode => {
    const chips: { token: string; label: string }[] = [{ token: '{{screen}}', label: t('settings.screens.export.chip.screen', 'screen id') }]
    if (splitBy) chips.push({ token: '{{split_value}}', label: t('settings.screens.export.chip.splitValue', "this workbook's group key") })
    if (chips.length === 0) return null
    const current = field === 'file_name_template' ? fileNameTemplate : archiveName
    return (
      <ChipStrip>
        <ChipLabel>{t('settings.screens.export.insert', 'Insert:')}</ChipLabel>
        {chips.map((c) => (
          <Chip
            key={c.token}
            type="button"
            onClick={() => setProp(field, appendToken(current, c.token))}
            title={c.label}
          >
            {c.token}
          </Chip>
        ))}
      </ChipStrip>
    )
  }

  const renderSheetNameChips = (idx: number, sheet: Row): ReactNode => {
    const sheetSplitBy = ((sheet.split_by as string | null | undefined) ?? '').trim()
    const chips: { token: string; label: string }[] = []
    if (splitBy) chips.push({ token: '{{split_value}}', label: t('settings.screens.export.chip.splitValue', "this workbook's group key") })
    if (sheetSplitBy) chips.push({ token: '{{sheet_value}}', label: t('settings.screens.export.chip.sheetValue', "this sheet's partition value") })
    chips.push({ token: '{{screen}}', label: t('settings.screens.export.chip.screen', 'screen id') })
    const current = (sheet.name as string | null | undefined) ?? ''
    return (
      <ChipStrip>
        <ChipLabel>{t('settings.screens.export.insert', 'Insert:')}</ChipLabel>
        {chips.map((c) => (
          <Chip
            key={c.token}
            type="button"
            onClick={() => patchSheet(idx, { name: appendToken(current, c.token) })}
            title={c.label}
          >
            {c.token}
          </Chip>
        ))}
      </ChipStrip>
    )
  }

  return (
    <Stack gap={16}>
      {/* ── Workbook-level fields ──────────────────────────────────────────────────────── */}
      <Stack gap={12}>
        <Field label={t('settings.screens.export.splitBy', 'Split by column')}>
          <SearchSelect
            value={splitBy}
            options={readColumnOptions}
            onChange={(v) => setProp('split_by', v || null)}
            anyLabel={t('settings.screens.export.noSplit', 'Single workbook (no split)') ?? ''}
            placeholder={readColumnOptions.length ? t('common.pick') : t('settings.screens.export.noColumns', 'No columns on the screen — type a column name')}
            allowCustom
          />
          <Hint>
            {readColumnOptions.length
              ? t('settings.screens.export.splitByHint',
                  "Optional. The screen's read query runs first; one xlsx is produced per distinct value of this column. Leave blank for a single xlsx with all sheets.")
              : t('settings.screens.export.noColumnsHint',
                  "The screen has no resolved columns. Type the column's name; the runtime matches case-insensitively against the read query's discovered columns.")}
          </Hint>
        </Field>

        <Field label={t('settings.screens.export.fileNameTemplate', 'File name template')}>
          <Input
            value={fileNameTemplate}
            onChange={(e) => setProp('file_name_template', e.target.value || null)}
            placeholder={splitBy ? `${readQuery}_{{split_value}}.xlsx` : `${readQuery}.xlsx`}
          />
          {renderWorkbookChips('file_name_template')}
          <Hint>
            {t('settings.screens.export.fileNameHint',
              'Per-xlsx file name. Defaults to ``<screen>_<split_value>.xlsx`` when splitting, ``<screen>.xlsx`` otherwise.')}
          </Hint>
        </Field>

        <Field label={t('settings.screens.export.archiveName', 'Archive name')}>
          <Input
            value={archiveName}
            onChange={(e) => setProp('archive_name', e.target.value || null)}
            placeholder={`${readQuery}.zip`}
            disabled={!splitBy}
          />
          {splitBy && renderWorkbookChips('archive_name')}
          <Hint>
            {splitBy
              ? t('settings.screens.export.archiveNameHint',
                  "Name of the .zip downloaded when multiple workbooks are produced. Defaults to ``<screen>.zip``.")
              : t('settings.screens.export.archiveNameOnlyOnSplit',
                  'Only used when ``Split by column`` is set.')}
          </Hint>
        </Field>
      </Stack>

      {/* ── Sheets list ───────────────────────────────────────────────────────────────── */}
      <Stack gap={10}>
        <Row gap={8} style={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <Sub>
            {t('settings.screens.export.sheetsHint',
              'Each sheet runs its own query and becomes a tab inside the xlsx. ParamBinds with ``source = "split_value"`` receive the workbook\'s group key.')}
          </Sub>
          <Button $variant="primary" $size="sm" onClick={addSheet}>
            <Plus size={13} /> {t('settings.screens.export.addSheet', 'Add sheet')}
          </Button>
        </Row>

        {sheets.length === 0 && (
          <Sub style={{ fontStyle: 'italic' }}>
            {t('settings.screens.export.noSheets', 'No sheets yet. Add at least one to make the export runnable.')}
          </Sub>
        )}

        {sheets.map((sheet, idx) => {
          const effConn = sheetEffectiveConnector(sheet)
          const sheetConnector = (sheet.connector as string | null | undefined) ?? ''
          const sheetQuery = (sheet.query as string | null | undefined) ?? ''
          const sheetSplitBy = (sheet.split_by as string | null | undefined) ?? ''
          const queryOpts = queryOptionsFor(effConn)
          const qMeta = queryMetaFor(effConn, sheetQuery)
          // ParamBindList source autocomplete: the workbook's split_value is always offered;
          // the read query's columns are useful for future per-row context but currently
          // not exposed to the export runtime — leaving them out keeps the suggestion clean.
          const paramSourceOptions: SearchSelectOption[] = [
            { value: 'split_value', label: t('settings.screens.export.chip.splitValue', "this workbook's group key"), mono: 'split_value' },
          ]
          // Target query's declared params + scanned :bind_params drive the param autocomplete.
          const paramOptions: SearchSelectOption[] = qMeta
            ? [
                ...qMeta.params.map((p) => ({ value: p.name, label: p.label || p.name, mono: p.name })),
                ...qMeta.bind_params
                  .filter((b) => !qMeta.params.some((p) => p.name === b))
                  .map((b) => ({ value: b, label: b, mono: b })),
              ]
            : []
          return (
            <SheetCard key={idx}>
              <SheetHeader>
                <SheetTitle>
                  {t('settings.screens.export.sheetN', 'Sheet {{n}}', { n: idx + 1 })}
                  {sheet.name ? <Tag $tone="blue" style={{ marginLeft: 10 }}>{sheet.name as string}</Tag> : null}
                </SheetTitle>
                <Button $variant="ghost" $size="sm" onClick={() => void removeSheet(idx)} title={t('common.delete')}>
                  <Trash2 size={13} />
                </Button>
              </SheetHeader>

              <Stack gap={10}>
                <Field label={t('settings.screens.export.sheetName', 'Sheet name')}>
                  <Input
                    value={(sheet.name as string | null | undefined) ?? ''}
                    onChange={(e) => patchSheet(idx, { name: e.target.value })}
                    placeholder={sheetSplitBy ? '{{sheet_value}}' : (splitBy ? '{{split_value}}' : 'Data')}
                  />
                  {renderSheetNameChips(idx, sheet)}
                  <Hint>
                    {t('settings.screens.export.sheetNameHint',
                      'Worksheet tab name. Excel allows 31 characters; the exporter truncates + sanitises automatically.')}
                  </Hint>
                </Field>

                <Row gap={10} style={{ alignItems: 'flex-end' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Field label={t('settings.screens.export.sheetConnector', 'Connector')}>
                      <SearchSelect
                        value={sheetConnector}
                        options={sqlConnectorOptions}
                        onChange={(v) => patchSheet(idx, { connector: v || null })}
                        anyLabel={t('settings.screens.export.sheetConnectorDefault', "Use screen's connector ({{c}})", { c: effectiveConnector }) ?? ''}
                        placeholder={t('common.pick')}
                      />
                    </Field>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Field label={t('settings.screens.export.sheetQuery', 'Query')}>
                      <Row gap={6} style={{ alignItems: 'center' }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <SearchSelect
                            value={sheetQuery}
                            options={queryOpts}
                            onChange={(v) => patchSheet(idx, { query: v || '' })}
                            placeholder={queryOpts.length ? t('common.pick') : t('settings.screens.editor.queries.pickConnectorFirst')}
                            loading={!effConn}
                          />
                        </div>
                        {sheetQuery && effConn && (
                          <Button
                            $variant="ghost"
                            $size="sm"
                            onClick={() => setEditQuery({ connector: effConn, queryName: sheetQuery })}
                            title={t('settings.editQuery.edit', 'Edit query')}
                          >
                            <Edit3 size={13} />
                          </Button>
                        )}
                      </Row>
                    </Field>
                  </div>
                </Row>

                <Field label={t('settings.screens.export.sheetSplitBy', 'Sheet split by column')}>
                  <Input
                    value={sheetSplitBy}
                    onChange={(e) => patchSheet(idx, { split_by: e.target.value || null })}
                    placeholder={t('settings.screens.export.sheetSplitByPh', 'Column name (e.g. APPS_NAME)') ?? ''}
                  />
                  <Hint>
                    {t('settings.screens.export.sheetSplitByHint',
                      "Optional. When set, the query's rows are partitioned by this column into one worksheet per distinct value (v1's ``tbl_sheet``). Reference ``{{sheet_value}}`` in the sheet name for the partition key.")}
                  </Hint>
                </Field>

                <Field label={t('settings.screens.export.paramBinds', 'Parameter bindings')}>
                  <ParamBindList
                    value={(sheet.param_binds as ParamBind[] | undefined) ?? []}
                    onChange={(next) => patchSheet(idx, { param_binds: next })}
                    sourceOptions={paramSourceOptions}
                    paramOptions={paramOptions}
                  />
                  <Hint>
                    {t('settings.screens.export.paramBindsHint',
                      "Bind the workbook's ``split_value`` (or a literal) into the query's ``:placeholder`` params.")}
                  </Hint>
                </Field>
              </Stack>
            </SheetCard>
          )
        })}
      </Stack>

      {editQuery && (
        <EditQueryModal
          connector={editQuery.connector}
          queryName={editQuery.queryName}
          onClose={() => setEditQuery(null)}
          onSaved={() => { setEditQuery(null); onQueryEdited?.() }}
        />
      )}
    </Stack>
  )
}
