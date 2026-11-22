// A job's "load a file" step (liberty/jobs/steps/file_load.py): a spreadsheet or a CSV into a
// table. The form follows the questions a person asks — where the file comes from, how to read
// it, which table it fills, what happens to the rows already there, what must be checked — each
// field under a short label, what it is for said below it. The table's columns and the
// connector's queries are offered as lists as soon as they can be read.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Input, SearchSelect, SearchSelectMulti, type SearchSelectOption } from '../../common'
import { fileSourceOf, fileSourcePatch, tableColumns, type FileSource } from '../../services/fileLoad'
import type { ConnectorMeta } from '../../types/connectors'
import type { StepConfig } from './types'
import { asOptions, connectorOptions, Field, FieldLabel, Grid, Hint, KeyValueEditor, ListInput, SchemaSelect, SubHead } from './stepFields'

export default function FileLoadForm({ step, patch, sqlConnectors }: {
  step: StepConfig
  patch: (p: Partial<StepConfig>) => void
  sqlConnectors: ConnectorMeta[]
}) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const set = (part: Record<string, unknown>) => patch(part as Partial<StepConfig>)
  const tgt = (f.target ?? {}) as Record<string, unknown>
  const setTarget = (part: Record<string, unknown>) => set({ target: { ...(f.target as object ?? {}), ...part } })
  const text = (key: string) => String(f[key] ?? '')
  const record = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' ? (v as Record<string, unknown>) : {})

  // Where the file comes from. The step stores a path, a folder, or neither: a choice just made
  // (its field still empty) is remembered here — the form is mounted per step, so for this step only.
  const [chosen, setChosen] = useState<FileSource | null>(null)
  const stored = fileSourceOf(f)
  const source: FileSource = stored !== 'upload' ? stored : (chosen ?? 'upload')
  const pickSource = (next: FileSource) => { setChosen(next); set({ ...fileSourcePatch(next, f) }) }

  // The table's columns, read once the table is named (a targeted lookup; none found = free text).
  const connector = String(tgt.connector ?? ''), schema = String(tgt.schema ?? ''), table = String(tgt.table ?? '')
  const [columns, setColumns] = useState<string[]>([])
  useEffect(() => {
    let on = true
    const timer = setTimeout(() => { void tableColumns(connector, schema, table).then((c) => { if (on) setColumns(c) }) }, 500)
    return () => { on = false; clearTimeout(timer) }
  }, [connector, schema, table])
  const columnOptions = useMemo(() => asOptions(columns), [columns])

  // The queries that may refuse the file: those of the connector named, else of the table's.
  const refuseOn = text('refuse_connector') || connector
  const refuseQueries = useMemo<SearchSelectOption[]>(() => {
    const meta = sqlConnectors.find((c) => c.name === refuseOn)
    return meta && meta.type === 'sql'
      ? meta.queries.filter((q) => !q.writable).map((q) => ({ value: q.name, label: q.description || q.label || q.name, mono: q.name }))
      : []
  }, [sqlConnectors, refuseOn])

  const replacing = f.load_mode === 'replace_partition'
  const partitions = Array.isArray(f.partition_by) ? (f.partition_by as string[]) : []

  return (
    <div data-file-load style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <SubHead>{t('nomaflow.load.file')}</SubHead>
      <Grid>
        <Field as="div" $span={3}>
          <FieldLabel>{t('nomaflow.load.source')}</FieldLabel>
          <SearchSelect value={source} onChange={(v) => pickSource((v || 'upload') as FileSource)}
            options={[
              { value: 'upload', label: t('nomaflow.load.sourceUpload') },
              { value: 'path', label: t('nomaflow.load.sourcePath') },
              { value: 'folder', label: t('nomaflow.load.sourceFolder') },
            ]} />
          {source === 'upload' && <Hint>{t('nomaflow.load.sourceUploadHint')}</Hint>}
        </Field>
        {source === 'path' && (
          <Field $span={3}>
            <FieldLabel>{t('nomaflow.load.path')}</FieldLabel>
            <Input value={text('path')} onChange={(e) => set({ path: e.target.value })} placeholder="/data/in/sales-${params.period}.xlsx" />
            <Hint>{t('nomaflow.load.pathHint')}</Hint>
          </Field>
        )}
        {source === 'folder' && (
          <>
            <Field $span={2}>
              <FieldLabel>{t('nomaflow.load.folder')}</FieldLabel>
              <Input value={text('watch_dir')} onChange={(e) => set({ watch_dir: e.target.value })} placeholder="/data/in/sales" />
            </Field>
            <Field>
              <FieldLabel>{t('nomaflow.load.pattern')}</FieldLabel>
              <Input value={String(f.watch_pattern ?? '*')} onChange={(e) => set({ watch_pattern: e.target.value || '*' })} placeholder="*.xlsx" />
            </Field>
            <Field as="div" $span={3}><Hint>{t('nomaflow.load.folderHint')}</Hint></Field>
          </>
        )}
      </Grid>

      <SubHead>{t('nomaflow.load.reading')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.load.sheet')}</FieldLabel>
          <Input value={text('sheet')} onChange={(e) => set({ sheet: e.target.value || undefined })} placeholder={t('nomaflow.load.sheetPlaceholder')} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.load.headerRow')}</FieldLabel>
          <Input type="number" min={1} value={String(f.header_row ?? 1)} onChange={(e) => set({ header_row: Number(e.target.value) || 1 })} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.load.delimiter')}</FieldLabel>
          <Input value={text('delimiter')} onChange={(e) => set({ delimiter: e.target.value || undefined })} placeholder={t('nomaflow.load.delimiterPlaceholder')} />
        </Field>
        <Field as="div" $span={2}>
          <FieldLabel>{t('nomaflow.load.decimal')}</FieldLabel>
          <SearchSelect value={String(f.decimal_separator ?? 'auto')} onChange={(v) => set({ decimal_separator: v || 'auto' })}
            options={[
              { value: 'auto', label: t('nomaflow.load.decimalAuto') },
              { value: '.', label: t('nomaflow.load.decimalDot') },
              { value: ',', label: t('nomaflow.load.decimalComma') },
            ]} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.load.encoding')}</FieldLabel>
          <Input value={String(f.encoding ?? 'utf-8-sig')} onChange={(e) => set({ encoding: e.target.value || 'utf-8-sig' })} placeholder="utf-8-sig" />
        </Field>
      </Grid>

      <SubHead>{t('nomaflow.load.table')}</SubHead>
      <Grid>
        <Field as="div">
          <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
          <SearchSelect sort value={connector} onChange={(v) => setTarget({ connector: v })} options={connectorOptions(sqlConnectors)} placeholder={t('common.pick')} />
        </Field>
        <Field as="div">
          <FieldLabel>{t('nomaflow.steps.schema')}</FieldLabel>
          <SchemaSelect connector={connector || null} value={schema} onChange={(v) => setTarget({ schema: v })} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.table')}</FieldLabel>
          <Input value={table} onChange={(e) => setTarget({ table: e.target.value })} placeholder="fact_sales" />
        </Field>
        <Field as="div" $span={3} data-load-columns>
          <FieldLabel>{t('nomaflow.load.columns')}</FieldLabel>
          <Hint>{t('nomaflow.load.columnsHint')}</Hint>
          <KeyValueEditor value={record(f.columns)} onChange={(v) => set({ columns: v })}
            heads={[t('nomaflow.load.columnOfTable'), t('nomaflow.load.columnOfFile')]}
            keyOptions={columnOptions} keyPlaceholder={t('nomaflow.load.columnOfTablePlaceholder')}
            valuePlaceholder={t('nomaflow.load.columnOfFilePlaceholder')} addLabel={t('nomaflow.load.columnsAdd')} />
        </Field>
        <Field as="div" $span={3} data-load-constants>
          <FieldLabel>{t('nomaflow.load.constants')}</FieldLabel>
          <Hint>{t('nomaflow.load.constantsHint')}</Hint>
          <KeyValueEditor value={record(f.constants)} onChange={(v) => set({ constants: v })}
            heads={[t('nomaflow.load.columnOfTable'), t('nomaflow.load.constantValue')]}
            keyOptions={columnOptions} keyPlaceholder={t('nomaflow.load.columnOfTablePlaceholder')}
            valuePlaceholder="${params.period}" addLabel={t('nomaflow.load.constantsAdd')} />
        </Field>
      </Grid>

      <SubHead>{t('nomaflow.load.loading')}</SubHead>
      <Grid>
        <Field as="div" $span={replacing ? 1 : 3}>
          <FieldLabel>{t('nomaflow.load.mode')}</FieldLabel>
          <SearchSelect value={String(f.load_mode ?? 'append')} onChange={(v) => set({ load_mode: v || 'append' })}
            options={[
              { value: 'append', label: t('nomaflow.load.modeAppend') },
              { value: 'replace_partition', label: t('nomaflow.load.modeReplace') },
            ]} />
        </Field>
        {replacing && (
          <Field as="div" $span={2}>
            <FieldLabel>{t('nomaflow.load.partition')}</FieldLabel>
            {columnOptions.length > 0
              ? <SearchSelectMulti allowCustom value={partitions} onChange={(v) => set({ partition_by: v })} options={columnOptions} placeholder={t('nomaflow.load.partitionPlaceholder')} />
              : <ListInput value={partitions} onChange={(v) => set({ partition_by: v })} placeholder={t('nomaflow.load.partitionPlaceholder')} />}
          </Field>
        )}
        {replacing && <Field as="div" $span={3}><Hint>{t('nomaflow.load.partitionHint')}</Hint></Field>}
        <Field as="div" $span={2}>
          <FieldLabel>{t('nomaflow.load.badCell')}</FieldLabel>
          <SearchSelect value={String(f.on_error ?? 'fail')} onChange={(v) => set({ on_error: v || 'fail' })}
            options={[
              { value: 'fail', label: t('nomaflow.load.badCellFail') },
              { value: 'skip', label: t('nomaflow.load.badCellSkip') },
            ]} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.batchSize')}</FieldLabel>
          <Input type="number" min={1} value={String(f.batch_size ?? 10000)} onChange={(e) => set({ batch_size: Number(e.target.value) || 10000 })} />
        </Field>
      </Grid>

      <SubHead>{t('nomaflow.load.checks')}</SubHead>
      <Grid>
        <Field as="div" $span={2} data-load-refuse>
          <FieldLabel>{t('nomaflow.load.refuse')}</FieldLabel>
          {refuseQueries.length > 0
            ? <SearchSelect sort clearable allowCustom value={text('refuse_query')} onChange={(v) => set({ refuse_query: v || undefined })} options={refuseQueries} placeholder={t('nomaflow.load.refuseNone')} />
            : <Input value={text('refuse_query')} onChange={(e) => set({ refuse_query: e.target.value || undefined })} placeholder={t('nomaflow.load.refuseNone')} />}
        </Field>
        <Field as="div">
          <FieldLabel>{t('nomaflow.load.refuseOn')}</FieldLabel>
          <SearchSelect sort clearable value={text('refuse_connector')} onChange={(v) => set({ refuse_connector: v || undefined })}
            options={connectorOptions(sqlConnectors)} placeholder={t('nomaflow.load.refuseOnTable')} />
        </Field>
        <Field as="div" $span={3}><Hint>{t('nomaflow.load.refuseHint')}</Hint></Field>
        <Field as="div">
          <FieldLabel>{t('nomaflow.load.control')}</FieldLabel>
          {columnOptions.length > 0
            ? <SearchSelect sort clearable allowCustom value={text('control_column')} onChange={(v) => set({ control_column: v || undefined })} options={columnOptions} placeholder={t('nomaflow.load.controlNone')} />
            : <Input value={text('control_column')} onChange={(e) => set({ control_column: e.target.value || undefined })} placeholder={t('nomaflow.load.controlNone')} />}
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.load.controlTotal')}</FieldLabel>
          <Input value={text('control_total')} onChange={(e) => set({ control_total: e.target.value || undefined })} placeholder="${params.total}" disabled={!text('control_column')} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.load.controlTolerance')}</FieldLabel>
          <Input type="number" min={0} step="0.01" value={String(f.control_tolerance ?? 0)} onChange={(e) => set({ control_tolerance: Number(e.target.value) || 0 })} disabled={!text('control_column')} />
        </Field>
        <Field as="div" $span={3}><Hint>{t('nomaflow.load.controlHint')}</Hint></Field>
      </Grid>
    </div>
  )
}
