// Per-widget editor for the visual dashboard builder. A modal that configures one widget —
// chart (inline via the SAME ChartSpecEditor the chart builder uses, or a reference to a saved
// chart), KPI, or table — with a live preview and the grid placement (width / height). Save
// hands the cleaned widget config back to the DashboardEditor; Cancel discards.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { X, BarChart3, Hash, Table as TableIcon, List as ListIcon, BarChartHorizontal, FileText, Link2, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Checkbox, Field, Input, SearchSelect, SpinnerRing, Tag, Textarea, useModals, type SearchSelectOption } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { ChartSpecEditor } from '../TableView/ChartSpecEditor'
import { ChartCanvas } from '../TableView/ChartCanvas'
import { resolveColumnName } from '../../services/chartData'
import { KpiWidget } from '../DashboardView/KpiWidget'
import { TableWidget } from '../DashboardView/TableWidget'
import { StatWidget } from '../DashboardView/StatWidget'
import { TextWidget } from '../DashboardView/TextWidget'
import { LinksWidget } from '../DashboardView/LinksWidget'
import { MENU_ICON_NAMES, iconFor } from '../../components/SidebarMenu'
import { ListWidget } from '../DashboardView/ListWidget'
import { BarsWidget } from '../DashboardView/BarsWidget'
import type { QueryResult } from '../../types/connectors'
import type { ChartSpec, SavedChartSpec } from '../../types/charts'
import { AGGREGATIONS, defaultChartSpec, fromSavedSpec, toSavedSpec } from '../../types/charts'
import type { BarsWidgetWire, KpiWidgetWire, ListWidgetWire, StatWidgetWire, TableWidgetWire, ValueFormat, TextWidgetWire, LinksWidgetWire, LinkWire } from '../../types/dashboards'
import { colors, fontSize, fonts, radius } from '../../theme'

const PREVIEW_LIMIT = 1000
export type WidgetKind = 'chart' | 'kpi' | 'table' | 'stat' | 'list' | 'bars' | 'text' | 'links'

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
  initial, scope, chartsCatalog, onSave, onClose,
}: {
  initial: Record<string, unknown>
  /** The dashboard's app: where a links widget's `menu` links are picked. */
  scope: string
  chartsCatalog: Record<string, { label?: string; connector?: string; query?: string; spec?: SavedChartSpec }>
  onSave: (widget: Record<string, unknown>) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const modals = useModals()
  const { connectors, menus } = useWorkspace()

  const [kind, setKind] = useState<WidgetKind>((initial.type as WidgetKind) || 'chart')
  const [label, setLabel] = useState(str(initial.label))
  const [description, setDescription] = useState(str(initial.description))
  const [colSpan, setColSpan] = useState(num(initial.col_span, 6))
  const [rowSpan, setRowSpan] = useState(num(initial.row_span, kind === 'kpi' || kind === 'stat' ? 1 : 2))
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
  const [comparePrevious, setComparePrevious] = useState(initial.compare_previous === true)
  // table
  const [columns, setColumns] = useState<string[]>(Array.isArray(initial.columns) ? (initial.columns as string[]) : [])
  const [maxRows, setMaxRows] = useState<string>(initial.max_rows != null ? String(initial.max_rows) : '')
  // the synthesis widgets (stat / list / bars): their presentation columns, all optional but the first
  const [plain, setPlain] = useState<Record<string, string>>({
    value_column: str(initial.value_column), format: str(initial.format), unit: str(initial.unit), sub: str(initial.sub),
    title_column: str(initial.title_column), subtitle_column: str(initial.subtitle_column), count_column: str(initial.count_column),
    count_label: str(initial.count_label), tone_column: str(initial.tone_column), tag_column: str(initial.tag_column),
    label_column: str(initial.label_column), sub_label_column: str(initial.sub_label_column), share: str(initial.share) || 'total', note: str(initial.note),
  })
  const setP = (k: string, v: string) => setPlain((cur) => ({ ...cur, [k]: v }))
  // text / links (no query behind them)
  const [markdown, setMarkdown] = useState(str(initial.markdown))
  const [links, setLinks] = useState<LinkWire[]>(Array.isArray(initial.links) ? (initial.links as LinkWire[]).map((l) => ({ ...l })) : [])
  const [linkColumns, setLinkColumns] = useState(num(initial.columns, 1))
  const setLink = (i: number, patch: Partial<LinkWire>) => setLinks((cur) => cur.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  // the app's menu leaves, for a `menu` link (the sidebar's own tree: what this user can see)
  const menuOpts: SearchSelectOption[] = useMemo(() => {
    const out: SearchSelectOption[] = []
    const walk = (nodes: { id: string; label: string; type?: string; items?: unknown[] }[], prefix: string) => {
      for (const n of nodes) {
        if (n.items) walk(n.items as typeof nodes, `${prefix}${n.label} › `)
        else if (n.type) out.push({ value: n.id, label: `${prefix}${n.label}`, mono: n.id })
      }
    }
    walk((menus?.[scope]?.items ?? []) as { id: string; label: string; type?: string; items?: unknown[] }[], '')
    return out
  }, [menus, scope])
  const iconOpts: SearchSelectOption[] = useMemo(() => MENU_ICON_NAMES.map((n) => { const I = iconFor(n); return { value: n, label: n, icon: I ? <I size={14} /> : undefined } }), [])
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
    if (description.trim()) base.description = description.trim()
    const put = (keys: string[]) => { for (const k of keys) if (plain[k]) base[k] = plain[k] }
    if (kind === 'chart') {
      if (chartMode === 'saved') {
        if (!savedChartId) return setError(t('settings.dash.errSavedChart'))
        base.chart = savedChartId
      } else {
        if (!connector || !query) return setError(t('settings.dash.errConnQuery'))
        if (!spec.x || spec.y.length === 0) return setError(t('settings.dash.errXY'))
        base.connector = connector; base.query = query; base.spec = toSavedSpec(spec)
      }
    } else if (kind === 'kpi') {
      if (!connector || !query) return setError(t('settings.dash.errConnQuery'))
      if (!column) return setError(t('settings.dash.errColumn'))
      base.connector = connector; base.query = query; base.column = column; base.aggregation = aggregation
      if (comparePrevious) base.compare_previous = true
    } else if (kind === 'text') {
      if (!markdown.trim()) return setError(t('settings.dash.errMarkdown'))
      base.markdown = markdown
    } else if (kind === 'links') {
      const clean = links.map((l) => ({
        ...(l.label?.trim() ? { label: l.label.trim() } : {}), ...(l.description?.trim() ? { description: l.description.trim() } : {}),
        ...(l.icon ? { icon: l.icon } : {}), ...(l.menu ? { menu: l.menu } : {}), ...(l.url?.trim() ? { url: l.url.trim() } : {}),
      }))
      if (!clean.length || clean.some((l) => !!l.menu === !!l.url || (l.url && !l.label))) return setError(t('settings.dash.errLinks'))
      base.links = clean; base.columns = linkColumns
    } else if (kind === 'stat') {
      if (!connector || !query) return setError(t('settings.dash.errConnQuery'))
      if (!plain.value_column) return setError(t('settings.dash.errValueColumn'))
      base.connector = connector; base.query = query
      put(['value_column', 'format', 'unit', 'sub'])
    } else if (kind === 'list') {
      if (!connector || !query) return setError(t('settings.dash.errConnQuery'))
      if (!plain.title_column) return setError(t('settings.dash.errTitleColumn'))
      base.connector = connector; base.query = query
      put(['title_column', 'subtitle_column', 'value_column', 'format', 'unit', 'count_column', 'count_label', 'tone_column', 'tag_column'])
      const mr = parseInt(maxRows, 10)
      if (Number.isFinite(mr) && mr > 0) base.max_rows = mr
    } else if (kind === 'bars') {
      if (!connector || !query) return setError(t('settings.dash.errConnQuery'))
      if (!plain.label_column || !plain.value_column) return setError(t('settings.dash.errLabelValue'))
      base.connector = connector; base.query = query
      put(['label_column', 'sub_label_column', 'value_column', 'format', 'unit', 'share', 'note'])
      const mr = parseInt(maxRows, 10)
      if (Number.isFinite(mr) && mr > 0) base.max_rows = mr
    } else {
      if (!connector || !query) return setError(t('settings.dash.errConnQuery'))
      base.connector = connector; base.query = query
      if (columns.length) base.columns = columns
      const mr = parseInt(maxRows, 10)
      if (Number.isFinite(mr) && mr > 0) base.max_rows = mr
    }
    onSave(base)
  }

  // Unsaved-changes guard (Save / Discard / Keep editing) — matches the chart + dashboard editors.
  const draftKey = JSON.stringify({ kind, label, description, colSpan, rowSpan, chartMode, savedChartId, spec, connector, query, column, aggregation, comparePrevious, columns, maxRows, plain, markdown, links, linkColumns })
  const initialKey = useRef<string | null>(null)
  if (initialKey.current === null) initialKey.current = draftKey
  const dirty = draftKey !== initialKey.current
  const requestClose = async () => {
    if (!dirty) { onClose(); return }
    const choice = await modals.choose<'discard' | 'save' | 'keep'>({
      title: t('settings.screens.designer.unsavedTitle'),
      message: t('settings.screens.designer.unsavedMsg'),
      options: [
        { value: 'discard', label: t('settings.screens.designer.discard'), variant: 'danger' },
        { value: 'save', label: t('common.save'), variant: 'primary' },
        { value: 'keep', label: t('settings.screens.designer.keepEditing'), variant: 'ghost', autoFocus: true },
      ],
      cancelValue: 'keep',
    })
    if (choice === 'save') save()
    else if (choice === 'discard') onClose()
  }

  const kpiPreview: KpiWidgetWire | null = kind === 'kpi' && connector && query && column
    ? { type: 'kpi', label: label || null, col_span: colSpan, row_span: rowSpan, connector, query, column, aggregation: aggregation as KpiWidgetWire['aggregation'] }
    : null
  const common = { label: label || null, description: description || null, col_span: colSpan, row_span: rowSpan, connector, query }
  const fmt = (plain.format || undefined) as ValueFormat | undefined
  const mr = parseInt(maxRows, 10)
  const statPreview: StatWidgetWire | null = kind === 'stat' && connector && query && plain.value_column
    ? { type: 'stat', ...common, value_column: plain.value_column, format: fmt, unit: plain.unit || undefined, sub: plain.sub || undefined }
    : null
  const listPreview: ListWidgetWire | null = kind === 'list' && connector && query && plain.title_column
    ? { type: 'list', ...common, title_column: plain.title_column, subtitle_column: plain.subtitle_column || undefined, value_column: plain.value_column || undefined, format: fmt, unit: plain.unit || undefined,
        count_column: plain.count_column || undefined, count_label: plain.count_label || undefined, tone_column: plain.tone_column || undefined, tag_column: plain.tag_column || undefined, max_rows: Number.isFinite(mr) && mr > 0 ? mr : 10 }
    : null
  const barsPreview: BarsWidgetWire | null = kind === 'bars' && connector && query && plain.label_column && plain.value_column
    ? { type: 'bars', ...common, label_column: plain.label_column, sub_label_column: plain.sub_label_column || undefined, value_column: plain.value_column, format: fmt, unit: plain.unit || undefined,
        share: plain.share === 'max' ? 'max' : 'total', note: plain.note || undefined, max_rows: Number.isFinite(mr) && mr > 0 ? mr : 10 }
    : null
  const formatOpts: SearchSelectOption[] = [{ value: 'number', label: t('settings.dash.fmtNumber') }, { value: 'integer', label: t('settings.dash.fmtInteger') }, { value: 'currency', label: t('settings.dash.fmtCurrency') }, { value: 'percent', label: t('settings.dash.fmtPercent') }]
  // A configured column name may be spelled in another case than the sample returns it (TOML
  // upper-case, Postgres lower-case): the pickers show the sample's spelling so the match is found.
  const canon = (v: string) => (sample && v ? resolveColumnName(sample, v) : v)
  const colPick = (key: string, lbl: string) => (
    <Field label={lbl}>
      <SearchSelect value={canon(plain[key])} onChange={(v) => setP(key, v)} options={colOpts} placeholder={t('chart.spec.pick')} anyLabel={t('common.none')} allowCustom />
    </Field>
  )
  const textPreview: TextWidgetWire | null = kind === 'text' && markdown.trim()
    ? { type: 'text', label: label || null, description: description || null, col_span: colSpan, row_span: rowSpan, markdown }
    : null
  const linksPreview: LinksWidgetWire | null = kind === 'links' && links.length
    ? { type: 'links', label: label || null, description: description || null, col_span: colSpan, row_span: rowSpan, app: scope, columns: linkColumns, links }
    : null
  const tablePreview: TableWidgetWire | null = kind === 'table' && connector && query
    ? { type: 'table', label: label || null, col_span: colSpan, row_span: rowSpan, connector, query, columns, ...(maxRows ? { max_rows: parseInt(maxRows, 10) } : {}) }
    : null

  return (
    <EditorModalShell
      title={t('settings.dash.widget')}
      onClose={() => void requestClose()}
      onSave={save}
      dirty={dirty}
      frameStyle={{ width: 'min(980px, 96vw)', height: 'min(700px, 94vh)' }}
      footerLeft={<Tag $tone="blue">{kind}</Tag>}
    >
      {error && <Banner $tone="error">{error}</Banner>}
          <KindBar>
            <KindBtn $active={kind === 'chart'} onClick={() => setKind('chart')}><BarChart3 size={14} /> {t('settings.dash.kindChart')}</KindBtn>
            <KindBtn $active={kind === 'kpi'} onClick={() => setKind('kpi')}><Hash size={14} /> {t('settings.dash.kindKpi')}</KindBtn>
            <KindBtn $active={kind === 'table'} onClick={() => setKind('table')}><TableIcon size={14} /> {t('settings.dash.kindTable')}</KindBtn>
            <KindBtn $active={kind === 'stat'} onClick={() => setKind('stat')}><Hash size={14} /> {t('settings.dash.kindStat')}</KindBtn>
            <KindBtn $active={kind === 'list'} onClick={() => setKind('list')}><ListIcon size={14} /> {t('settings.dash.kindList')}</KindBtn>
            <KindBtn $active={kind === 'bars'} onClick={() => setKind('bars')}><BarChartHorizontal size={14} /> {t('settings.dash.kindBars')}</KindBtn>
            <KindBtn $active={kind === 'text'} onClick={() => setKind('text')}><FileText size={14} /> {t('settings.dash.kindText')}</KindBtn>
            <KindBtn $active={kind === 'links'} onClick={() => setKind('links')}><Link2 size={14} /> {t('settings.dash.kindLinks')}</KindBtn>
          </KindBar>

          <Grid3>
            <Field label={t('settings.dash.wLabel')}>
              <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t('settings.dash.wLabel')} />
            </Field>
            <Field label={t('settings.dash.width')}>
              <Input type="number" min={1} max={12} value={colSpan} onChange={(e) => setColSpan(Math.min(12, Math.max(1, Number(e.target.value) || 1)))} />
            </Field>
            <Field label={t('settings.dash.height')}>
              <Input type="number" min={1} max={8} value={rowSpan} onChange={(e) => setRowSpan(Math.min(8, Math.max(1, Number(e.target.value) || 1)))} />
            </Field>
          </Grid3>
          <Field label={t('settings.dash.wDescription')}>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('settings.dash.wDescriptionHint')} />
          </Field>

          {kind === 'text' && (
            <>
              <Field label={t('settings.dash.markdown')}>
                <Textarea value={markdown} onChange={(e) => setMarkdown(e.target.value)} rows={8} placeholder={t('settings.dash.markdownHint')} />
              </Field>
              <PreviewWrap>
                {textPreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><TextWidget widget={textPreview} /></div>
                  : <Msg>{t('settings.dash.errMarkdown')}</Msg>}
              </PreviewWrap>
            </>
          )}

          {kind === 'links' && (
            <>
              <Grid3>
                <Field label={t('settings.dash.linkColumns')}>
                  <Input type="number" min={1} max={4} value={linkColumns} onChange={(e) => setLinkColumns(Math.min(4, Math.max(1, Number(e.target.value) || 1)))} />
                </Field>
              </Grid3>
              {links.map((l, i) => (
                <Grid3 key={i} style={{ alignItems: 'end' }}>
                  <Field label={t('settings.dash.linkMenu')}>
                    <SearchSelect sort value={l.menu ?? ''} onChange={(v) => setLink(i, { menu: v || null, ...(v ? { url: null } : {}) })} options={menuOpts} anyLabel={t('common.none')} />
                  </Field>
                  <Field label={t('settings.dash.linkUrl')}>
                    <Input value={l.url ?? ''} onChange={(e) => setLink(i, { url: e.target.value, ...(e.target.value ? { menu: null } : {}) })} placeholder="https://…" />
                  </Field>
                  <Field label={t('settings.dash.linkIcon')}>
                    <SearchSelect value={l.icon ?? ''} onChange={(v) => setLink(i, { icon: v || null })} options={iconOpts} anyLabel={t('common.none')} />
                  </Field>
                  <Field label={t('settings.dash.linkLabel')}>
                    <Input value={l.label ?? ''} onChange={(e) => setLink(i, { label: e.target.value })} placeholder={t('settings.dash.linkLabelHint')} />
                  </Field>
                  <Field label={t('settings.dash.linkDescription')}>
                    <Input value={l.description ?? ''} onChange={(e) => setLink(i, { description: e.target.value })} />
                  </Field>
                  <div><Button $size="sm" $variant="ghost" onClick={() => setLinks((cur) => cur.filter((_, j) => j !== i))}><Trash2 size={13} /> {t('common.delete')}</Button></div>
                </Grid3>
              ))}
              <div><Button $size="sm" $variant="ghost" onClick={() => setLinks((cur) => [...cur, {}])}><Plus size={13} /> {t('settings.dash.addLink')}</Button></div>
              <PreviewWrap>
                {linksPreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><LinksWidget widget={linksPreview} /></div>
                  : <Msg>{t('settings.dash.errLinks')}</Msg>}
              </PreviewWrap>
            </>
          )}

          {/* chart: saved vs inline */}
          {kind === 'chart' && (
            <KindBar>
              <KindBtn $active={chartMode === 'inline'} onClick={() => setChartMode('inline')}>{t('settings.dash.inline')}</KindBtn>
              <KindBtn $active={chartMode === 'saved'} onClick={() => setChartMode('saved')}>{t('settings.dash.savedChart')}</KindBtn>
            </KindBar>
          )}
          {kind === 'chart' && chartMode === 'saved' ? (
            <Field label={t('settings.dash.savedChart')}>
              <SearchSelect sort value={savedChartId} onChange={setSavedChartId} options={savedChartOpts}
                placeholder={savedChartOpts.length ? t('chart.spec.pick') : t('settings.dash.noCharts')} />
            </Field>
          ) : (
            <Grid2>
              <Field label={t('settings.charts.connector')}>
                <SearchSelect sort value={connector} onChange={(v) => { setConnector(v); setQuery('') }} options={connectorOpts} placeholder={t('chart.spec.pick')} allowCustom />
              </Field>
              <Field label={t('settings.charts.query')}>
                <SearchSelect sort value={query} onChange={setQuery} options={queryOpts}
                  placeholder={connector ? t('chart.spec.pick') : t('settings.charts.pickConnFirst')} allowCustom />
              </Field>
            </Grid2>
          )}

          {/* kind-specific config */}
          {kind === 'chart' && chartMode === 'inline' && (
            <PreviewWrap>
              {!connector || !query ? <Msg>{t('settings.charts.previewNeedQuery')}</Msg>
                : sampleErr ? <Msg style={{ color: colors.red.main }}>{sampleErr}</Msg>
                : !sample ? <Msg><SpinnerRing size={20} thickness={2} /></Msg>
                : (<>
                    <ChartSpecEditor result={sample} spec={spec} onChange={setSpec} />
                    <div style={{ flex: 1, minHeight: 200, marginTop: 10, display: 'flex' }}>
                      <ChartCanvas result={sample} spec={spec} connector={connector} emptyMessage={t('settings.charts.previewSetXY')} />
                    </div>
                  </>)}
            </PreviewWrap>
          )}

          {kind === 'kpi' && (
            <>
              <Grid2>
                <Field label={t('settings.dash.column')}>
                  <SearchSelect value={canon(column)} onChange={setColumn} options={colOpts} placeholder={t('chart.spec.pick')} allowCustom />
                </Field>
                <Field label={t('chart.spec.aggregation')}>
                  <SearchSelect value={aggregation} onChange={setAggregation} options={aggOpts} />
                </Field>
              </Grid2>
              <Checkbox checked={comparePrevious} onChange={setComparePrevious} label={t('settings.dash.comparePrevious')} />
              <PreviewWrap>
                {kpiPreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><KpiWidget widget={kpiPreview} filters={[]} filterValues={{}} /></div>
                  : <Msg>{t('settings.dash.kpiNeed')}</Msg>}
              </PreviewWrap>
            </>
          )}

          {kind === 'stat' && (
            <>
              <Grid3>
                {colPick('value_column', t('settings.dash.valueColumn'))}
                <Field label={t('settings.dash.format')}>
                  <SearchSelect value={plain.format} onChange={(v) => setP('format', v)} options={formatOpts} anyLabel={t('settings.dash.fmtNumber')} />
                </Field>
                <Field label={t('settings.dash.unit')}>
                  <Input value={plain.unit} onChange={(e) => setP('unit', e.target.value)} placeholder="€, users…" />
                </Field>
              </Grid3>
              <Field label={t('settings.dash.sub')}>
                <Input value={plain.sub} onChange={(e) => setP('sub', e.target.value)} placeholder={t('settings.dash.subHint')} />
              </Field>
              <PreviewWrap>
                {statPreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><StatWidget widget={statPreview} filters={[]} filterValues={{}} /></div>
                  : <Msg>{t('settings.dash.statNeed')}</Msg>}
              </PreviewWrap>
            </>
          )}

          {kind === 'list' && (
            <>
              <Grid3>
                {colPick('title_column', t('settings.dash.titleColumn'))}
                {colPick('subtitle_column', t('settings.dash.subtitleColumn'))}
                {colPick('tone_column', t('settings.dash.toneColumn'))}
              </Grid3>
              <Grid3>
                {colPick('value_column', t('settings.dash.valueColumn'))}
                <Field label={t('settings.dash.format')}>
                  <SearchSelect value={plain.format} onChange={(v) => setP('format', v)} options={formatOpts} anyLabel={t('settings.dash.fmtNumber')} />
                </Field>
                <Field label={t('settings.dash.unit')}>
                  <Input value={plain.unit} onChange={(e) => setP('unit', e.target.value)} placeholder="€, users…" />
                </Field>
              </Grid3>
              <Grid3>
                {colPick('count_column', t('settings.dash.countColumn'))}
                <Field label={t('settings.dash.countLabel')}>
                  <Input value={plain.count_label} onChange={(e) => setP('count_label', e.target.value)} placeholder="{n} invoices" />
                </Field>
                {colPick('tag_column', t('settings.dash.tagColumn'))}
              </Grid3>
              <Grid2>
                <Field label={t('settings.dash.maxRowsShown')}>
                  <Input type="number" min={1} max={100} value={maxRows} onChange={(e) => setMaxRows(e.target.value)} placeholder="10" />
                </Field>
                <div />
              </Grid2>
              <PreviewWrap>
                {listPreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><ListWidget widget={listPreview} filters={[]} filterValues={{}} /></div>
                  : <Msg>{t('settings.dash.listNeed')}</Msg>}
              </PreviewWrap>
            </>
          )}

          {kind === 'bars' && (
            <>
              <Grid3>
                {colPick('label_column', t('settings.dash.labelColumn'))}
                {colPick('sub_label_column', t('settings.dash.subLabelColumn'))}
                {colPick('value_column', t('settings.dash.valueColumn'))}
              </Grid3>
              <Grid3>
                <Field label={t('settings.dash.format')}>
                  <SearchSelect value={plain.format} onChange={(v) => setP('format', v)} options={formatOpts} anyLabel={t('settings.dash.fmtNumber')} />
                </Field>
                <Field label={t('settings.dash.unit')}>
                  <Input value={plain.unit} onChange={(e) => setP('unit', e.target.value)} placeholder="€, users…" />
                </Field>
                <Field label={t('settings.dash.share')}>
                  <SearchSelect value={plain.share} onChange={(v) => setP('share', v)} options={[{ value: 'total', label: t('settings.dash.shareTotal') }, { value: 'max', label: t('settings.dash.shareMax') }]} />
                </Field>
              </Grid3>
              <Grid2>
                <Field label={t('settings.dash.note')}>
                  <Input value={plain.note} onChange={(e) => setP('note', e.target.value)} />
                </Field>
                <Field label={t('settings.dash.maxRowsShown')}>
                  <Input type="number" min={1} max={100} value={maxRows} onChange={(e) => setMaxRows(e.target.value)} placeholder="10" />
                </Field>
              </Grid2>
              <PreviewWrap>
                {barsPreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><BarsWidget widget={barsPreview} filters={[]} filterValues={{}} /></div>
                  : <Msg>{t('settings.dash.barsNeed')}</Msg>}
              </PreviewWrap>
            </>
          )}

          {kind === 'table' && (
            <>
              <Field label={t('settings.dash.columns')}>
                <Chips>
                  {columns.map((c, i) => (
                    <Chip key={c}>{c}<button type="button" onClick={() => setColumns(columns.filter((_, j) => j !== i))} title={t('common.delete')}><X size={11} /></button></Chip>
                  ))}
                  <div style={{ minWidth: 180 }}>
                    <SearchSelect value="" onChange={(v) => { if (v && !columns.includes(v)) setColumns([...columns, v]) }}
                      options={colOpts.filter((o) => !columns.some((c) => c.toLowerCase() === o.value.toLowerCase()))} placeholder={t('settings.dash.addColumn')} allowCustom />
                  </div>
                </Chips>
              </Field>
              <Grid2>
                <Field label={t('settings.dash.maxRows')}>
                  <Input type="number" min={1} value={maxRows} onChange={(e) => setMaxRows(e.target.value)} placeholder="15" />
                </Field>
                <div />
              </Grid2>
              <PreviewWrap>
                {tablePreview ? <div style={{ flex: 1, minHeight: 0, display: 'flex' }}><TableWidget widget={tablePreview} filters={[]} filterValues={{}} /></div>
                  : <Msg>{t('settings.dash.tableNeed')}</Msg>}
              </PreviewWrap>
            </>
          )}
    </EditorModalShell>
  )
}

export default WidgetEditorModal
