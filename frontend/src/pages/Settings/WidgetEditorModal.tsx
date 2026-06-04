// Per-widget editor for the visual dashboard builder. A modal that configures one widget —
// chart (inline via the SAME ChartSpecEditor the chart builder uses, or a reference to a saved
// chart), KPI, or table — with a live preview and the grid placement (width / height). Save
// hands the cleaned widget config back to the DashboardEditor; Cancel discards.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { X, BarChart3, Hash, Table as TableIcon } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Overlay, Modal, ModalBody, ModalFooter, Button, Banner, Field, Input, SearchSelect, SpinnerRing, Tag, useModals, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { ChartSpecEditor } from '../TableView/ChartSpecEditor'
import { ChartCanvas } from '../TableView/ChartCanvas'
import { KpiWidget } from '../DashboardView/KpiWidget'
import { TableWidget } from '../DashboardView/TableWidget'
import type { QueryResult } from '../../types/connectors'
import type { ChartSpec, SavedChartSpec } from '../../types/charts'
import { AGGREGATIONS, defaultChartSpec, fromSavedSpec, toSavedSpec } from '../../types/charts'
import type { KpiWidgetWire, TableWidgetWire } from '../../types/dashboards'
import { colors, fontSize, fonts, radius } from '../../theme'

const PREVIEW_LIMIT = 1000
export type WidgetKind = 'chart' | 'kpi' | 'table'

const Box = styled(Modal)`width: min(980px, 96vw); height: min(700px, 94vh);`
const Header = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;
  & .title { font-size: ${fontSize.lg}; font-weight: 700; color: ${colors.text.primary}; font-family: ${fonts.sans}; }
`
const CloseBtn = styled.button`
  margin-left: auto; display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px;
  border-radius: ${radius.md}; border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const KindBar = styled.div`display: flex; gap: 6px; flex-wrap: wrap;`
const KindBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; border-radius: ${radius.md}; cursor: pointer;
  font-size: ${fontSize.base}; font-family: ${fonts.sans}; font-weight: 600;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  &:hover { border-color: ${colors.blue.border}; }
`
const Grid2 = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 12px;`
const Grid3 = styled.div`display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;`
const PreviewWrap = styled.div`
  margin-top: 6px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 10px;
  min-height: 260px; display: flex; flex-direction: column;
`
const Msg = styled.div`flex: 1; display: flex; align-items: center; justify-content: center; color: ${colors.text.muted}; font-size: ${fontSize.sm}; text-align: center; padding: 16px;`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 6px; align-items: center;`
const Chip = styled.span`
  display: inline-flex; align-items: center; gap: 5px; padding: 3px 4px 3px 9px; border-radius: ${radius.sm};
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  & button { border: 0; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 1px; }
  & button:hover { color: ${colors.red.main}; }
`

const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d)
const str = (v: unknown) => (typeof v === 'string' ? v : '')

export function WidgetEditorModal({
  initial, chartsCatalog, onSave, onClose,
}: {
  initial: Record<string, unknown>
  chartsCatalog: Record<string, { label?: string; connector?: string; query?: string; spec?: SavedChartSpec }>
  onSave: (widget: Record<string, unknown>) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const modals = useModals()
  const { connectors } = useWorkspace()

  const [kind, setKind] = useState<WidgetKind>((initial.type as WidgetKind) || 'chart')
  const [label, setLabel] = useState(str(initial.label))
  const [colSpan, setColSpan] = useState(num(initial.col_span, 6))
  const [rowSpan, setRowSpan] = useState(num(initial.row_span, kind === 'kpi' ? 1 : 2))
  // chart
  const [chartMode, setChartMode] = useState<'inline' | 'saved'>(initial.chart ? 'saved' : 'inline')
  const [savedChartId, setSavedChartId] = useState(str(initial.chart))
  const [spec, setSpec] = useState<ChartSpec>(initial.spec ? fromSavedSpec(initial.spec as SavedChartSpec) : defaultChartSpec())
  // shared sql
  const [connector, setConnector] = useState(str(initial.connector))
  const [query, setQuery] = useState(str(initial.query))
  // kpi
  const [column, setColumn] = useState(str(initial.column))
  const [aggregation, setAggregation] = useState(str(initial.aggregation) || 'count')
  // table
  const [columns, setColumns] = useState<string[]>(Array.isArray(initial.columns) ? (initial.columns as string[]) : [])
  const [maxRows, setMaxRows] = useState<string>(initial.max_rows != null ? String(initial.max_rows) : '')
  const [error, setError] = useState<string | null>(null)

  // Sample of the chosen query — drives the column pickers + the chart inline builder/preview.
  const [sample, setSample] = useState<QueryResult | null>(null)
  const [sampleErr, setSampleErr] = useState<string | null>(null)
  useEffect(() => {
    setSample(null); setSampleErr(null)
    if (!connector || !query) return
    let cancelled = false
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}?_limit=${PREVIEW_LIMIT}`)
      .then((r) => { if (!cancelled) setSample(r) })
      .catch((e) => { if (!cancelled) setSampleErr(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [connector, query])

  const sqlConnectors = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql'), [connectors])
  const connectorOpts: SearchSelectOption[] = sqlConnectors.map((c) => ({ value: c.name, label: c.name, mono: c.name }))
  const queryOpts: SearchSelectOption[] = useMemo(() => {
    const c = sqlConnectors.find((x) => x.name === connector)
    return (c?.queries ?? []).map((q) => ({ value: q.name, label: q.description || q.label || q.name, mono: q.name }))
  }, [sqlConnectors, connector])
  const colOpts: SearchSelectOption[] = useMemo(
    () => (sample?.columns ?? []).filter((c) => !c.hidden).map((c) => ({ value: c.name, label: c.label ?? c.name, mono: c.name })),
    [sample],
  )
  const savedChartOpts: SearchSelectOption[] = Object.entries(chartsCatalog).map(([id, c]) => ({ value: id, label: c.label || id, mono: id }))
  const aggOpts: SearchSelectOption[] = AGGREGATIONS.filter((a) => a !== 'none').map((a) => ({ value: a, label: t(`chart.agg.${a}`) }))

  const save = () => {
    setError(null)
    const base: Record<string, unknown> = { type: kind, col_span: colSpan, row_span: rowSpan }
    if (label.trim()) base.label = label.trim()
    if (kind === 'chart') {
      if (chartMode === 'saved') {
        if (!savedChartId) return setError(t('settings.dash.errSavedChart', 'Pick a saved chart.'))
        base.chart = savedChartId
      } else {
        if (!connector || !query) return setError(t('settings.dash.errConnQuery', 'Pick a connector and a query.'))
        if (!spec.x || spec.y.length === 0) return setError(t('settings.dash.errXY', 'Set the X column and at least one Y series.'))
        base.connector = connector; base.query = query; base.spec = toSavedSpec(spec)
      }
    } else if (kind === 'kpi') {
      if (!connector || !query) return setError(t('settings.dash.errConnQuery', 'Pick a connector and a query.'))
      if (!column) return setError(t('settings.dash.errColumn', 'Pick the column to aggregate.'))
      base.connector = connector; base.query = query; base.column = column; base.aggregation = aggregation
    } else {
      if (!connector || !query) return setError(t('settings.dash.errConnQuery', 'Pick a connector and a query.'))
      base.connector = connector; base.query = query
      if (columns.length) base.columns = columns
      const mr = parseInt(maxRows, 10)
      if (Number.isFinite(mr) && mr > 0) base.max_rows = mr
    }
    onSave(base)
  }

  // Unsaved-changes guard (Save / Discard / Keep editing) — matches the chart + dashboard editors.
  const draftKey = JSON.stringify({ kind, label, colSpan, rowSpan, chartMode, savedChartId, spec, connector, query, column, aggregation, columns, maxRows })
  const initialKey = useRef<string | null>(null)
  if (initialKey.current === null) initialKey.current = draftKey
  const dirty = draftKey !== initialKey.current
  const requestClose = async () => {
    if (!dirty) { onClose(); return }
    const choice = await modals.choose<'discard' | 'save' | 'keep'>({
      title: t('settings.screens.designer.unsavedTitle', 'Unsaved changes'),
      message: t('settings.screens.designer.unsavedMsg', 'You have unsaved changes. Save them, discard them, or keep editing?'),
      options: [
        { value: 'discard', label: t('settings.screens.designer.discard', 'Discard'), variant: 'danger' },
        { value: 'save', label: t('common.save'), variant: 'primary' },
        { value: 'keep', label: t('settings.screens.designer.keepEditing', 'Keep editing'), variant: 'ghost', autoFocus: true },
      ],
      cancelValue: 'keep',
    })
    if (choice === 'save') save()
    else if (choice === 'discard') onClose()
  }

  const kpiPreview: KpiWidgetWire | null = kind === 'kpi' && connector && query && column
    ? { type: 'kpi', label: label || null, col_span: colSpan, row_span: rowSpan, connector, query, column, aggregation: aggregation as KpiWidgetWire['aggregation'] }
    : null
  const tablePreview: TableWidgetWire | null = kind === 'table' && connector && query
    ? { type: 'table', label: label || null, col_span: colSpan, row_span: rowSpan, connector, query, columns, ...(maxRows ? { max_rows: parseInt(maxRows, 10) } : {}) }
    : null

  // No backdrop-click-to-close — outside clicks must not discard edits (Cancel / Escape).
  return (
    <Overlay>
      <Box onClick={(e) => e.stopPropagation()}>
        <Header>
          <span className="title">{t('settings.dash.widget', 'Widget')}</span>
          <CloseBtn onClick={() => void requestClose()} title={t('common.close')}><X size={16} /></CloseBtn>
        </Header>
        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          <KindBar>
            <KindBtn $active={kind === 'chart'} onClick={() => setKind('chart')}><BarChart3 size={14} /> {t('settings.dash.kindChart', 'Chart')}</KindBtn>
            <KindBtn $active={kind === 'kpi'} onClick={() => setKind('kpi')}><Hash size={14} /> {t('settings.dash.kindKpi', 'KPI')}</KindBtn>
            <KindBtn $active={kind === 'table'} onClick={() => setKind('table')}><TableIcon size={14} /> {t('settings.dash.kindTable', 'Table')}</KindBtn>
          </KindBar>

          <Grid3>
            <Field label={t('settings.dash.wLabel', 'Label')}>
              <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t('settings.dash.wLabel', 'Label')} />
            </Field>
            <Field label={t('settings.dash.width', 'Width (1–12)')}>
              <Input type="number" min={1} max={12} value={colSpan} onChange={(e) => setColSpan(Math.min(12, Math.max(1, Number(e.target.value) || 1)))} />
            </Field>
            <Field label={t('settings.dash.height', 'Height (rows)')}>
              <Input type="number" min={1} max={8} value={rowSpan} onChange={(e) => setRowSpan(Math.min(8, Math.max(1, Number(e.target.value) || 1)))} />
            </Field>
          </Grid3>

          {/* chart: saved vs inline */}
          {kind === 'chart' && (
            <KindBar>
              <KindBtn $active={chartMode === 'inline'} onClick={() => setChartMode('inline')}>{t('settings.dash.inline', 'Inline spec')}</KindBtn>
              <KindBtn $active={chartMode === 'saved'} onClick={() => setChartMode('saved')}>{t('settings.dash.savedChart', 'Saved chart')}</KindBtn>
            </KindBar>
          )}
          {kind === 'chart' && chartMode === 'saved' ? (
            <Field label={t('settings.dash.savedChart', 'Saved chart')}>
              <SearchSelect value={savedChartId} onChange={setSavedChartId} options={savedChartOpts}
                placeholder={savedChartOpts.length ? t('chart.spec.pick', 'Pick…') : t('settings.dash.noCharts', 'No saved charts — create one in the Charts tab')} />
            </Field>
          ) : (
            <Grid2>
              <Field label={t('settings.charts.connector', 'Connector')}>
                <SearchSelect value={connector} onChange={(v) => { setConnector(v); setQuery('') }} options={connectorOpts} placeholder={t('chart.spec.pick', 'Pick…')} allowCustom />
              </Field>
              <Field label={t('settings.charts.query', 'Query')}>
                <SearchSelect value={query} onChange={setQuery} options={queryOpts}
                  placeholder={connector ? t('chart.spec.pick', 'Pick…') : t('settings.charts.pickConnFirst', 'Pick a connector first')} allowCustom />
              </Field>
            </Grid2>
          )}

          {/* kind-specific config */}
          {kind === 'chart' && chartMode === 'inline' && (
            <PreviewWrap>
              {!connector || !query ? <Msg>{t('settings.charts.previewNeedQuery', 'Set a connector + query to build the chart.')}</Msg>
                : sampleErr ? <Msg style={{ color: colors.red.main }}>{sampleErr}</Msg>
                : !sample ? <Msg><SpinnerRing size={20} thickness={2} /></Msg>
                : (<>
                    <ChartSpecEditor result={sample} spec={spec} onChange={setSpec} />
                    <div style={{ flex: 1, minHeight: 200, marginTop: 10, display: 'flex' }}>
                      <ChartCanvas result={sample} spec={spec} connector={connector} emptyMessage={t('settings.charts.previewSetXY', 'Set X + at least one Y series.')} />
                    </div>
                  </>)}
            </PreviewWrap>
          )}

          {kind === 'kpi' && (
            <>
              <Grid2>
                <Field label={t('settings.dash.column', 'Column')}>
                  <SearchSelect value={column} onChange={setColumn} options={colOpts} placeholder={t('chart.spec.pick', 'Pick…')} allowCustom />
                </Field>
                <Field label={t('chart.spec.aggregation', 'Aggregation')}>
                  <SearchSelect value={aggregation} onChange={setAggregation} options={aggOpts} />
                </Field>
              </Grid2>
              <PreviewWrap>
                {kpiPreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><KpiWidget widget={kpiPreview} filters={[]} filterValues={{}} /></div>
                  : <Msg>{t('settings.dash.kpiNeed', 'Pick connector, query and column to preview.')}</Msg>}
              </PreviewWrap>
            </>
          )}

          {kind === 'table' && (
            <>
              <Field label={t('settings.dash.columns', 'Columns (blank = all)')}>
                <Chips>
                  {columns.map((c, i) => (
                    <Chip key={c}>{c}<button type="button" onClick={() => setColumns(columns.filter((_, j) => j !== i))} title={t('common.delete')}><X size={11} /></button></Chip>
                  ))}
                  <div style={{ minWidth: 180 }}>
                    <SearchSelect value="" onChange={(v) => { if (v && !columns.includes(v)) setColumns([...columns, v]) }}
                      options={colOpts.filter((o) => !columns.includes(o.value))} placeholder={t('settings.dash.addColumn', 'Add column…')} allowCustom />
                  </div>
                </Chips>
              </Field>
              <Grid2>
                <Field label={t('settings.dash.maxRows', 'Max rows (blank = all)')}>
                  <Input type="number" min={1} value={maxRows} onChange={(e) => setMaxRows(e.target.value)} placeholder="15" />
                </Field>
                <div />
              </Grid2>
              <PreviewWrap>
                {tablePreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><TableWidget widget={tablePreview} filters={[]} filterValues={{}} /></div>
                  : <Msg>{t('settings.dash.tableNeed', 'Pick a connector and query to preview.')}</Msg>}
              </PreviewWrap>
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Tag $tone="blue">{kind}</Tag>
          <div style={{ flex: 1 }} />
          <Button $size="sm" $variant="ghost" onClick={() => void requestClose()}>{t('common.cancel', 'Cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={save}>{t('common.save')}</Button>
        </ModalFooter>
      </Box>
    </Overlay>
  )
}

export default WidgetEditorModal
