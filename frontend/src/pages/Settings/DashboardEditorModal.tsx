// Visual dashboard builder — a fullscreen editor that shows the REAL widget grid (live charts /
// KPIs / tables, same renderers DashboardView uses) with edit chrome on each cell: resize (width
// 1–12 / height in rows), reorder (← →), edit (opens the per-widget WidgetEditorModal — which
// reuses the chart builder for chart widgets), and delete. Add widgets from the toolbar. Plus the
// dashboard meta (label / description) and the filter bar. Save persists the whole dashboard.
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { X, Plus, Pencil, Trash2, ChevronLeft, ChevronRight, BarChart3, Hash, Table as TableIcon, LayoutDashboard } from 'lucide-react'
import { Overlay, Modal, Button, Input, Field, Banner, useModals } from '../../common'
import { ChartWidget } from '../DashboardView/ChartWidget'
import { KpiWidget } from '../DashboardView/KpiWidget'
import { TableWidget } from '../DashboardView/TableWidget'
import type { DashboardWidget, ChartWidgetWire, KpiWidgetWire, TableWidgetWire } from '../../types/dashboards'
import type { SavedChartSpec } from '../../types/charts'
import { WidgetEditorModal } from './WidgetEditorModal'
import { colors, fontSize, fonts, radius } from '../../theme'

type Raw = Record<string, unknown>
const ROW_PX = 150

const Box = styled(Modal)`width: 98vw; height: 96vh; max-width: 98vw;`
const Header = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;
  & .id { font-family: ${fonts.mono}; font-size: ${fontSize.md}; color: ${colors.text.muted}; }
`
const CloseBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px;
  border-radius: ${radius.md}; border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const Body = styled.div`flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 14px;`
const MetaRow = styled.div`display: grid; grid-template-columns: 1fr 2fr; gap: 12px;`
const AddBar = styled.div`display: flex; gap: 8px; align-items: center; flex-wrap: wrap;`
const Grid = styled.div`
  display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; grid-auto-rows: ${ROW_PX}px;
`
const Cell = styled.div<{ $cs: number; $rs: number }>`
  position: relative; grid-column: span ${({ $cs }) => $cs}; grid-row: span ${({ $rs }) => $rs};
  min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 6px;
  border: 1px dashed transparent; border-radius: ${radius.lg};
  &:hover { border-color: ${colors.blue.border}; }
  &:hover .chrome { opacity: 1; }
`
const Title = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-weight: 600; letter-spacing: 0.04em; padding: 0 4px; flex-shrink: 0;`
const Host = styled.div`flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column;`
const Chrome = styled.div`
  position: absolute; top: 4px; right: 4px; z-index: 5; display: flex; gap: 2px; opacity: 0; transition: opacity 0.12s;
  background: var(--bg-modal); border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 2px;
  & button { border: 0; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 22px; border-radius: ${radius.sm}; font-size: 10px; font-weight: 700; }
  & button:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
  & button.del:hover { background: ${colors.red.bg}; color: ${colors.red.main}; }
  & .sep { width: 1px; background: ${colors.border}; margin: 2px 1px; }
`
const Placeholder = styled.div`
  flex: 1; display: flex; align-items: center; justify-content: center; text-align: center; padding: 12px;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: ${colors.bg.card};
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
`
const FiltersPanel = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 12px; display: flex; flex-direction: column; gap: 10px;`
const FilterRow = styled.div`display: grid; grid-template-columns: repeat(7, 1fr) auto; gap: 6px; align-items: center;`
const Footer = styled.div`display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-top: 1px solid ${colors.border}; flex-shrink: 0;`

const snum = (v: unknown, d: number) => (typeof v === 'number' ? v : d)
const sstr = (v: unknown) => (typeof v === 'string' ? v : '')

type ChartsCatalog = Record<string, { label?: string; connector?: string; query?: string; spec?: SavedChartSpec }>
type Kind = 'chart' | 'kpi' | 'table'

/** Infer a widget's kind. The parsed-config endpoint drops the ``type`` discriminator when it
 *  equals the field default (``exclude_defaults``), so a loaded widget often has no ``type`` — we
 *  recover it from the distinctive fields: a chart has ``spec`` / ``chart``; a KPI has ``column``;
 *  everything else (connector + query) is a table. Normalising on load means the canvas renders a
 *  live preview AND the saved widgets carry ``type`` again (the discriminated union needs it). */
function widgetType(w: Raw): Kind {
  const tp = sstr(w.type)
  if (tp === 'chart' || tp === 'kpi' || tp === 'table') return tp
  if (w.spec !== undefined || w.chart !== undefined) return 'chart'
  if (w.column !== undefined) return 'kpi'
  return 'table'
}

/** Raw config widget → the resolved wire shape the DashboardView renderers expect. Returns null
 *  when it can't render yet (incomplete, or a saved-chart ref that doesn't resolve). */
function toWire(w: Raw, charts: ChartsCatalog): DashboardWidget | null {
  const common = { label: sstr(w.label) || null, col_span: snum(w.col_span, 6), row_span: snum(w.row_span, 2) }
  const kind = widgetType(w)
  if (kind === 'chart') {
    if (w.chart) {
      const c = charts[sstr(w.chart)]
      if (!c?.connector || !c?.query || !c?.spec) return null
      return { type: 'chart', ...common, chart_id: sstr(w.chart), connector: c.connector, query: c.query, spec: c.spec } as ChartWidgetWire
    }
    if (w.connector && w.query && w.spec) return { type: 'chart', ...common, connector: sstr(w.connector), query: sstr(w.query), spec: w.spec as SavedChartSpec } as ChartWidgetWire
    return null
  }
  if (kind === 'kpi') {
    if (!w.connector || !w.query || !w.column) return null
    return { type: 'kpi', ...common, connector: sstr(w.connector), query: sstr(w.query), column: sstr(w.column), aggregation: (sstr(w.aggregation) || 'count') as KpiWidgetWire['aggregation'] } as KpiWidgetWire
  }
  // table
  if (!w.connector || !w.query) return null
  return { type: 'table', ...common, connector: sstr(w.connector), query: sstr(w.query), columns: Array.isArray(w.columns) ? (w.columns as string[]) : [], ...(w.max_rows ? { max_rows: snum(w.max_rows, 0) } : {}) } as TableWidgetWire
}

const KIND_ICON = { chart: BarChart3, kpi: Hash, table: TableIcon } as const

export function DashboardEditorModal({
  initial, scope, chartsCatalog, onSave, onClose,
}: {
  initial: Raw
  /** The owning connector scope — dashboards are stored at `[dashboards.<scope>.<id>]`. Fixed by
   *  the builder (move a dashboard between apps by delete + re-add). Widgets may read any connector. */
  scope: string
  chartsCatalog: ChartsCatalog
  onSave: (id: string, record: Raw) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const modals = useModals()
  const [id] = useState(sstr(initial.id))
  const [label, setLabel] = useState(sstr(initial.label))
  const [description, setDescription] = useState(sstr(initial.description))
  // Normalise on load: restore the `type` the parsed-config endpoint dropped (see widgetType), so
  // the canvas renders live previews and saved widgets keep their discriminator.
  const [widgets, setWidgets] = useState<Raw[]>(Array.isArray(initial.widgets) ? (initial.widgets as Raw[]).map((w) => ({ ...w, type: widgetType(w) })) : [])
  const [filters, setFilters] = useState<Raw[]>(Array.isArray(initial.filters) ? (initial.filters as Raw[]).map((f) => ({ ...f, options: { ...(f.options as Raw) } })) : [])
  const [editIdx, setEditIdx] = useState<number | null>(null)
  const [newIdx, setNewIdx] = useState<number | null>(null)  // a just-added widget; cancel removes it
  const [error, setError] = useState<string | null>(null)

  const patchWidget = (i: number, patch: Raw) => setWidgets((ws) => ws.map((w, j) => (j === i ? { ...w, ...patch } : w)))
  const resize = (i: number, dCol: number, dRow: number) => setWidgets((ws) => ws.map((w, j) =>
    j === i ? { ...w, col_span: Math.min(12, Math.max(1, snum(w.col_span, 6) + dCol)), row_span: Math.min(8, Math.max(1, snum(w.row_span, 2) + dRow)) } : w))
  const move = (i: number, dir: -1 | 1) => setWidgets((ws) => {
    const j = i + dir; if (j < 0 || j >= ws.length) return ws
    const next = [...ws]; [next[i], next[j]] = [next[j], next[i]]; return next
  })
  const del = (i: number) => setWidgets((ws) => ws.filter((_, j) => j !== i))

  const addWidget = (kind: 'chart' | 'kpi' | 'table') => {
    const w: Raw = { type: kind, col_span: kind === 'kpi' ? 3 : 6, row_span: kind === 'kpi' ? 1 : 2 }
    setWidgets((ws) => { const next = [...ws, w]; setNewIdx(next.length - 1); setEditIdx(next.length - 1); return next })
  }

  const onWidgetSave = (w: Raw) => { if (editIdx != null) patchWidget(editIdx, { ...w }); setEditIdx(null); setNewIdx(null) }
  const onWidgetCancel = () => { if (newIdx != null) del(newIdx); setEditIdx(null); setNewIdx(null) }

  const addFilter = () => setFilters((fs) => [...fs, { id: '', label: '', dictionary_key: '', options: { connector: '', query: '', value_column: '', label_column: '' } }])
  const patchFilter = (i: number, patch: Raw) => setFilters((fs) => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)))
  const patchFilterOpt = (i: number, patch: Raw) => setFilters((fs) => fs.map((f, j) => (j === i ? { ...f, options: { ...(f.options as Raw), ...patch } } : f)))

  const save = () => {
    setError(null)
    if (!id) return setError(t('settings.dash.errId', 'Dashboard id missing.'))
    const rec: Raw = { id, label: label.trim() || id, widgets }
    if (description.trim()) rec.description = description.trim()
    // `connector` (the owning scope) is the dict path key — the builder places the record under
    // it, so we don't write it into the body.
    const cleanFilters = filters.filter((f) => sstr(f.id) && sstr(f.label))
    if (cleanFilters.length) rec.filters = cleanFilters
    onSave(id, rec)
  }

  // Unsaved-changes guard (Save / Discard / Keep editing) — snapshot the editable state on first
  // render, compare on close. Same dialog the screen visual builder uses.
  const draftKey = JSON.stringify({ label, description, widgets, filters })
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

  const editingInitial = editIdx != null ? widgets[editIdx] : null

  return (
    <Overlay onClick={() => void requestClose()}>
      <Box onClick={(e) => e.stopPropagation()}>
        <Header>
          <LayoutDashboard size={17} color={colors.blue.main} />
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t('settings.dash.title', 'Dashboard title')} style={{ maxWidth: 320 }} />
          <span className="id">[dashboards.{scope}.{id}]</span>
          <div style={{ flex: 1 }} />
          <CloseBtn onClick={() => void requestClose()} title={t('common.close')}><X size={16} /></CloseBtn>
        </Header>

        <Body>
          {error && <Banner $tone="error">{error}</Banner>}
          <MetaRow>
            <Field label={t('settings.dash.dLabel', 'Title')}>
              <Input value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
            <Field label={t('settings.dash.description', 'Description')}>
              <Input value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
          </MetaRow>

          <AddBar>
            <span style={{ fontSize: fontSize.sm, color: colors.text.muted, fontWeight: 600 }}>{t('settings.dash.addWidget', 'Add widget')}:</span>
            <Button $size="sm" $variant="ghost" onClick={() => addWidget('chart')}><BarChart3 size={13} /> {t('settings.dash.kindChart', 'Chart')}</Button>
            <Button $size="sm" $variant="ghost" onClick={() => addWidget('kpi')}><Hash size={13} /> {t('settings.dash.kindKpi', 'KPI')}</Button>
            <Button $size="sm" $variant="ghost" onClick={() => addWidget('table')}><TableIcon size={13} /> {t('settings.dash.kindTable', 'Table')}</Button>
          </AddBar>

          {widgets.length === 0 ? (
            <Placeholder style={{ minHeight: 160 }}>{t('settings.dash.noWidgets', 'No widgets yet — add a chart, KPI or table above.')}</Placeholder>
          ) : (
            <Grid>
              {widgets.map((w, i) => {
                const kind = widgetType(w)
                const Icon = KIND_ICON[kind] ?? BarChart3
                const wire = toWire(w, chartsCatalog)
                const cs = snum(w.col_span, 6), rs = snum(w.row_span, 2)
                return (
                  <Cell key={i} $cs={cs} $rs={rs}>
                    {sstr(w.label) && <Title>{sstr(w.label)}</Title>}
                    <Chrome className="chrome">
                      <button title={t('settings.dash.narrower', 'Narrower')} onClick={() => resize(i, -1, 0)}>W−</button>
                      <button title={t('settings.dash.wider', 'Wider')} onClick={() => resize(i, +1, 0)}>W+</button>
                      <button title={t('settings.dash.shorter', 'Shorter')} onClick={() => resize(i, 0, -1)}>H−</button>
                      <button title={t('settings.dash.taller', 'Taller')} onClick={() => resize(i, 0, +1)}>H+</button>
                      <span className="sep" />
                      <button title={t('settings.dash.moveLeft', 'Move earlier')} onClick={() => move(i, -1)}><ChevronLeft size={13} /></button>
                      <button title={t('settings.dash.moveRight', 'Move later')} onClick={() => move(i, +1)}><ChevronRight size={13} /></button>
                      <span className="sep" />
                      <button title={t('common.edit', 'Edit')} onClick={() => { setEditIdx(i); setNewIdx(null) }}><Pencil size={13} /></button>
                      <button className="del" title={t('common.delete')} onClick={() => del(i)}><Trash2 size={13} /></button>
                    </Chrome>
                    <Host>
                      {wire ? (
                        wire.type === 'chart' ? <ChartWidget widget={wire} filters={[]} filterValues={{}} />
                          : wire.type === 'table' ? <TableWidget widget={wire} filters={[]} filterValues={{}} />
                          : <KpiWidget widget={wire} filters={[]} filterValues={{}} />
                      ) : (
                        <Placeholder><span><Icon size={18} /><br />{t('settings.dash.configurePrompt', 'Click ✎ to configure this {{kind}}', { kind })}</span></Placeholder>
                      )}
                    </Host>
                  </Cell>
                )
              })}
            </Grid>
          )}

          {/* Filters — the bar above the live dashboard. Compact form list (id / label /
              dictionary key + the options query that fills the dropdown). */}
          <FiltersPanel>
            <AddBar>
              <span style={{ fontSize: fontSize.sm, color: colors.text.muted, fontWeight: 600 }}>{t('settings.dash.filters', 'Filters')}</span>
              <Button $size="sm" $variant="ghost" onClick={addFilter}><Plus size={13} /> {t('settings.dash.addFilter', 'Add filter')}</Button>
            </AddBar>
            {filters.map((f, i) => {
              const o = (f.options as Raw) ?? {}
              return (
                <FilterRow key={i}>
                  <Input placeholder="id" value={sstr(f.id)} onChange={(e) => patchFilter(i, { id: e.target.value })} />
                  <Input placeholder={t('settings.dash.dLabel', 'Label')} value={sstr(f.label)} onChange={(e) => patchFilter(i, { label: e.target.value })} />
                  <Input placeholder="dictionary_key" value={sstr(f.dictionary_key)} onChange={(e) => patchFilter(i, { dictionary_key: e.target.value })} />
                  <Input placeholder="connector" value={sstr(o.connector)} onChange={(e) => patchFilterOpt(i, { connector: e.target.value })} />
                  <Input placeholder="query" value={sstr(o.query)} onChange={(e) => patchFilterOpt(i, { query: e.target.value })} />
                  <Input placeholder="value_column" value={sstr(o.value_column)} onChange={(e) => patchFilterOpt(i, { value_column: e.target.value })} />
                  <Input placeholder="label_column" value={sstr(o.label_column)} onChange={(e) => patchFilterOpt(i, { label_column: e.target.value })} />
                  <CloseBtn onClick={() => setFilters((fs) => fs.filter((_, j) => j !== i))} title={t('common.delete')}><Trash2 size={14} /></CloseBtn>
                </FilterRow>
              )
            })}
          </FiltersPanel>
        </Body>

        <Footer>
          <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.dash.widgetCount', '{{n}} widget(s)', { n: widgets.length })}</span>
          <div style={{ flex: 1 }} />
          <Button $size="sm" $variant="ghost" onClick={() => void requestClose()}>{t('common.cancel', 'Cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={save}>{t('common.save')}</Button>
        </Footer>
      </Box>

      {editingInitial && (
        <WidgetEditorModal
          initial={editingInitial}
          chartsCatalog={chartsCatalog}
          onSave={onWidgetSave}
          onClose={onWidgetCancel}
        />
      )}
    </Overlay>
  )
}

export default DashboardEditorModal
