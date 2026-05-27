// Phase 8 slice 1 — chart widget for the TableView.
//
// Renders a result set as a bar / line / area / pie chart, with an inline spec editor above
// (Type · X column · Y column(s) · Aggregation). The spec is persisted to localStorage per
// `(connector, query)` — a later slice will lift it into `config/charts.toml` for shared,
// versioned chart specs (Phase 8 slice 2).
//
// Recharts is the underlying lib — declarative, theme-friendly via CSS vars. We pull series
// colours from the theme palette (blue / green / orange / purple / red / yellow) so light/dark
// theme swapping just works through the shared --*-main CSS vars.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Plus, Save, X } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { Button, Field, SearchSelect, type SearchSelectOption } from '../../common'
import { isNumericColumn } from '../../services/chartData'
import type { Column, QueryResult } from '../../types/connectors'
import type { Aggregation, ChartSpec, ChartType, SavedChartSpec, YAxisSide } from '../../types/charts'
import { AGGREGATIONS, CHART_TYPES, defaultChartSpec, fromSavedSpec } from '../../types/charts'
import { ChartCanvas, seriesColorAt } from './ChartCanvas'
import { SaveChartModal } from './SaveChartModal'
import { colors as themeColors, radius } from '../../theme'

const Frame = styled.div`
  display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 12px;
`
const SpecBar = styled.div`
  display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end;
`
const SpecCell = styled.div`min-width: 140px;`
// Series editor — one row per Y column (column / colour / axis / remove), plus an "+ Add"
// button at the end. Compact enough to sit inline with the rest of the SpecBar but breaks
// onto its own line for charts with more than two series.
const SeriesList = styled.div`
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  padding: 6px; border: 1px dashed ${themeColors.border}; border-radius: ${radius.md};
  min-width: 280px; flex: 1; min-height: 38px;
`
const SeriesRow = styled.div`
  display: flex; align-items: center; gap: 4px;
  padding: 2px 4px 2px 2px; border-radius: ${radius.sm};
  background: var(--bg-modal); border: 1px solid ${themeColors.border};
`
// Native colour picker masked by the swatch — operators click the coloured square and the
// OS / browser picker opens directly underneath. Keeps the inline editor compact without a
// nested popover.
const ColorSwatch = styled.label<{ $color: string }>`
  position: relative; display: inline-block;
  width: 18px; height: 18px; border-radius: ${radius.sm};
  background: ${({ $color }) => $color}; border: 1px solid rgba(0, 0, 0, 0.2);
  cursor: pointer; flex-shrink: 0;
  input { position: absolute; inset: 0; opacity: 0; cursor: pointer; padding: 0; border: 0; }
`
// L / R toggle. Compact two-segment button that swaps the axis side in one click — the
// dominant interaction (most charts are single-axis, occasionally an operator flips one
// series to the right; the segmented control communicates that succinctly).
const AxisToggle = styled.button<{ $active: boolean }>`
  font-family: inherit; font-size: 11px; line-height: 1; padding: 3px 6px;
  border: 1px solid ${({ $active }) => $active ? themeColors.blue.main : themeColors.border};
  background: ${({ $active }) => $active ? themeColors.blue.main : 'transparent'};
  color: ${({ $active }) => $active ? '#fff' : themeColors.text.secondary};
  border-radius: ${radius.sm}; cursor: pointer;
  &:hover { border-color: ${themeColors.blue.main}; }
`
const IconBtn = styled.button`
  font-family: inherit; padding: 2px; border: 0; background: transparent;
  color: ${themeColors.text.muted}; cursor: pointer; display: inline-flex;
  border-radius: ${radius.sm};
  &:hover { color: ${themeColors.text.primary}; background: var(--hover-subtle, rgba(255,255,255,0.06)); }
`

const STORAGE_PREFIX = 'liberty:chart-spec:'

export interface ChartViewProps {
  result: QueryResult
  connector: string
  query: string
  /** Carrying the screen lets us read ``screen.chart_id`` and pre-fill the
   *  spec from charts.toml on first render. Null / no screen → fall back to
   *  the localStorage-seeded default (the pre-Phase-F behaviour). */
  screen?: { chart_id?: string | null } | null
}

export function ChartView({ result, connector, query, screen }: ChartViewProps) {
  const { t } = useTranslation()
  const allCols = useMemo(() => result.columns.filter((c) => !c.hidden), [result])
  const numericCols = useMemo(() => allCols.filter(isNumericColumn), [allCols])

  // Saved-charts catalog — fetched ONCE on mount, refetched after the Save
  // modal lands a new one (the modal's onSaved callback bumps reloadKey).
  // Drives two UX surfaces in this component:
  //   1. The auto-load on ``screen.chart_id`` — when the operator linked a
  //      default chart in the Screen Designer, its spec pre-fills here.
  //   2. The Load picker — operator can switch to ANY saved chart matching
  //      this (connector, query) for ad-hoc exploration; survives Save.
  // Failure is silent: empty catalog → no auto-load, no picker — same fallback
  // the rest of the chart UX uses (a no-charts-installed deployment works).
  const linkedChartId = (screen?.chart_id ?? '') || null
  type SavedChart = { id?: string; label?: string; description?: string; connector?: string; query?: string; spec?: SavedChartSpec }
  type ChartsDoc = { charts: Record<string, SavedChart> }
  const [allSavedCharts, setAllSavedCharts] = useState<Record<string, SavedChart>>({})
  const [chartsReloadKey, setChartsReloadKey] = useState(0)
  useEffect(() => {
    let cancelled = false
    import('../../api/client').then(({ api }) => {
      api.get<ChartsDoc>('/admin/config/charts/parsed')
        .then((r) => { if (!cancelled) setAllSavedCharts(r.charts ?? {}) })
        .catch(() => { /* silent */ })
    })
    return () => { cancelled = true }
  }, [chartsReloadKey])
  // Auto-load spec from screen.chart_id — runs once per linkedChartId
  // change AFTER the catalog lands. Operator who manually picks something
  // else from the Load picker (changes ``spec`` themselves) is respected —
  // we only seed at first mount.
  const linkedChartSpec = useMemo<ChartSpec | null>(() => {
    if (!linkedChartId) return null
    const entry = allSavedCharts[linkedChartId]
    return entry?.spec ? fromSavedSpec(entry.spec) : null
  }, [linkedChartId, allSavedCharts])
  // Saved charts that target THIS (connector, query) — what the Load picker
  // shows. Charts saved against other queries are filtered out so the dropdown
  // stays relevant.
  const matchingSavedCharts = useMemo(() => {
    return Object.entries(allSavedCharts)
      .filter(([, c]) => c.connector === connector && c.query === query)
      .map(([id, c]) => ({ id, label: c.label || id, spec: c.spec }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [allSavedCharts, connector, query])

  // Persist the spec per `(connector, query)`. The seed order is:
  //   1. Saved chart spec (when ``screen.chart_id`` resolves) — operator's
  //      curated default lands every time the screen opens.
  //   2. localStorage (this session's tweaks) — operator-specific fiddling.
  //   3. seedSpec from the result columns — last resort, picks something
  //      plausible so the Chart tab isn't blank.
  const storageKey = `${STORAGE_PREFIX}${connector}/${query}`
  const initialSpec = useMemo(
    () => linkedChartSpec ?? loadSpec(storageKey) ?? seedSpec(allCols, numericCols),
    [linkedChartSpec, storageKey, allCols, numericCols],
  )
  // Component state held in localStorage indirectly: a `setSpec` writes through.
  // (A plain `useState(initialSpec)` with `useEffect` to write would also work; this is terser.)
  const [spec, setSpec] = useStoredSpec(storageKey, initialSpec)

  // Repair stale Y columns (someone changed the query → an old Y col no longer exists). Strip
  // missing ones; if all gone, reseed from the new result. We track the SURVIVING indices so
  // colours / axis assignments stay aligned with the trimmed y (a naive ``.filter`` on each
  // independently would re-pair them out of order).
  const cleanSpec: ChartSpec = useMemo(() => {
    const colNames = new Set(allCols.map((c) => c.name))
    const survivingIdx: number[] = []
    spec.y.forEach((n, i) => { if (colNames.has(n)) survivingIdx.push(i) })
    const y = survivingIdx.map((i) => spec.y[i])
    const colors = spec.colors ? survivingIdx.map((i) => spec.colors![i] ?? '') : undefined
    const yAxis = spec.yAxis ? survivingIdx.map((i) => spec.yAxis![i] ?? 'left') : undefined
    const x = colNames.has(spec.x) ? spec.x : (allCols[0]?.name ?? '')
    if (y.length === 0 && numericCols.length > 0) {
      // All series went stale → reseed minimally; drop the orphaned colours/axes too so the
      // new series start from defaults rather than inheriting a former column's choices.
      return { ...spec, x, y: [numericCols[0].name], colors: undefined, yAxis: undefined }
    }
    return { ...spec, x, y, colors, yAxis }
  }, [spec, allCols, numericCols])

  const typeOpts: SearchSelectOption[] = CHART_TYPES.map((c) => ({ value: c, label: t(`chart.type.${c}`) }))
  const aggOpts: SearchSelectOption[] = AGGREGATIONS.map((a) => ({ value: a, label: t(`chart.agg.${a}`) }))
  const colOpts: SearchSelectOption[] = allCols.map((c) => ({ value: c.name, label: c.label ?? c.name }))

  /** A series' display name — used for the default Save modal label (the chart canvas does its own
   *  series-label resolution internally). */
  const seriesName = (col: Column): string => col.label ?? col.name

  // Save-chart modal — only shown for superusers (the underlying admin endpoint is gated).
  const { user } = useAuth()
  const canSave = !!user?.is_superuser
  const [saveOpen, setSaveOpen] = useState(false)
  const canSubmit = canSave && !!cleanSpec.x && cleanSpec.y.length > 0

  return (
    <Frame>
      <SpecBar>
        <SpecCell>
          <Field label={t('chart.spec.type')}>
            <SearchSelect value={cleanSpec.type} onChange={(v) => setSpec({ ...cleanSpec, type: v as ChartType })} options={typeOpts} />
          </Field>
        </SpecCell>
        <SpecCell>
          <Field label={t('chart.spec.x')}>
            <SearchSelect value={cleanSpec.x} onChange={(v) => setSpec({ ...cleanSpec, x: v })} options={colOpts} placeholder={t('chart.spec.pick')} />
          </Field>
        </SpecCell>
        {/* Series editor — multi-Y / per-series colour / per-series axis side. Replaces
            the single-Y dropdown so operators can build the v1 dual-Y chart shape (e.g.
            INTERNAL_COUNT on the left, EXTERNAL_COUNT on the right, each in its own
            chosen colour) without hand-editing TOML. Single-series charts still look the
            same — the row collapses to one chip + the +Add button. Pie charts only use
            the first Y so the axis toggle is hidden in pie mode (cosmetic only). */}
        <Field label={t('chart.spec.series', 'Series')}>
          <SeriesList>
            {cleanSpec.y.map((yName, i) => (
              <SeriesRow key={`${yName}-${i}`}>
                <SearchSelect
                  value={yName}
                  onChange={(v) => setSpec(updateSeriesColumn(cleanSpec, i, v))}
                  options={colOpts} placeholder={t('chart.spec.pick')}
                />
                <ColorSwatch $color={seriesColorAt(i, cleanSpec)}
                  title={t('chart.spec.colorTitle', 'Pick this series’ colour')}>
                  <input type="color"
                    value={normaliseHex(cleanSpec.colors?.[i] ?? seriesColorAt(i, cleanSpec))}
                    onChange={(e) => setSpec(updateSeriesColor(cleanSpec, i, e.target.value))} />
                </ColorSwatch>
                {cleanSpec.type !== 'pie' && (
                  <>
                    <AxisToggle type="button"
                      $active={(cleanSpec.yAxis?.[i] ?? 'left') === 'left'}
                      onClick={() => setSpec(updateSeriesAxis(cleanSpec, i, 'left'))}
                      title={t('chart.spec.axisLeft', 'Bind to left Y axis')}>L</AxisToggle>
                    <AxisToggle type="button"
                      $active={cleanSpec.yAxis?.[i] === 'right'}
                      onClick={() => setSpec(updateSeriesAxis(cleanSpec, i, 'right'))}
                      title={t('chart.spec.axisRight', 'Bind to right Y axis')}>R</AxisToggle>
                  </>
                )}
                {cleanSpec.y.length > 1 && (
                  <IconBtn type="button" onClick={() => setSpec(removeSeries(cleanSpec, i))}
                    title={t('chart.spec.removeSeries', 'Remove this series')}>
                    <X size={12} />
                  </IconBtn>
                )}
              </SeriesRow>
            ))}
            {/* + Add — only when at least one column isn't already in spec.y, since adding
                a duplicate would render an invisible overlay series. Pie collapses to one
                Y so the button is hidden in pie mode. */}
            {cleanSpec.type !== 'pie' && hasUnusedColumn(cleanSpec.y, allCols) && (
              <IconBtn type="button" onClick={() => setSpec(addSeries(cleanSpec, allCols))}
                title={t('chart.spec.addSeries', 'Add a series')}>
                <Plus size={14} />
              </IconBtn>
            )}
          </SeriesList>
        </Field>
        <SpecCell>
          <Field label={t('chart.spec.aggregation')}>
            <SearchSelect value={cleanSpec.aggregation} onChange={(v) => setSpec({ ...cleanSpec, aggregation: v as Aggregation })} options={aggOpts} />
          </Field>
        </SpecCell>
        {/* Load picker — surfaces saved charts that target THIS (connector,
            query) so operators can switch between curated views without
            re-typing the spec. Hidden when no saved charts match (empty
            charts.toml or no matches for this query → nothing to load). */}
        {matchingSavedCharts.length > 0 && (
          <SpecCell style={{ marginLeft: 'auto' }}>
            <Field label={t('chart.load.label', 'Load saved')}>
              <SearchSelect
                value=""  /* unselected — the picker is for switching, not state */
                onChange={(id) => {
                  if (!id) return
                  const hit = matchingSavedCharts.find((c) => c.id === id)
                  if (hit?.spec) setSpec(fromSavedSpec(hit.spec))
                }}
                options={matchingSavedCharts.map((c) => ({
                  value: c.id, label: c.label, mono: c.id,
                }))}
                placeholder={t('chart.load.placeholder', 'Apply a saved chart…')}
              />
            </Field>
          </SpecCell>
        )}
        {canSave && (
          // Pushed to the right end of the spec bar — with a Load picker
          // present, the Save button stays flush right; without one, the
          // ``margin-left: auto`` keeps the four-dropdown group flushed left.
          <div style={{ marginLeft: matchingSavedCharts.length > 0 ? undefined : 'auto', alignSelf: 'flex-end' }}>
            <Button $size="sm" $variant="ghost" onClick={() => setSaveOpen(true)} disabled={!canSubmit}
              title={canSubmit ? t('chart.save.buttonTitle') : t('chart.save.buttonDisabled')}>
              <Save size={13} /> {t('chart.save.button')}
            </Button>
          </div>
        )}
      </SpecBar>

      <ChartCanvas
        result={result} spec={cleanSpec} connector={connector}
        emptyMessage={t('chart.empty.pickCols')} noDataMessage={t('chart.empty.noData')}
      />
      {saveOpen && (
        // Default the label to a friendly suggestion built from the X / first-Y column labels —
        // "Users per Application" rather than the operator typing it from scratch. They can edit.
        <SaveChartModal
          connector={connector} query={query} spec={cleanSpec}
          defaultLabel={defaultSavedLabel(cleanSpec, allCols, seriesName)}
          onSaved={() => {
            setSaveOpen(false)
            // Refetch the catalog so the just-saved chart appears in the
            // Load picker without a page reload.
            setChartsReloadKey((k) => k + 1)
          }}
          onCancel={() => setSaveOpen(false)}
        />
      )}
    </Frame>
  )
}

/** Make a reasonable "Save as" label from the chart's X / Y columns — "Y per X" with the
 *  resolved column labels. The operator edits it in the modal; this just saves a few keystrokes. */
function defaultSavedLabel(spec: ChartSpec, allCols: Column[], seriesName: (c: Column) => string): string {
  const xCol = allCols.find((c) => c.name === spec.x)
  const yCol = allCols.find((c) => c.name === spec.y[0])
  const xLabel = xCol ? seriesName(xCol) : spec.x
  const yLabel = yCol ? seriesName(yCol) : spec.y[0]
  return yLabel ? `${yLabel} per ${xLabel}` : ''
}

// ── persistence helpers ─────────────────────────────────────────────────────────────

function loadSpec(storageKey: string): ChartSpec | null {
  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    return parsed as ChartSpec
  } catch {
    return null
  }
}

function saveSpec(storageKey: string, spec: ChartSpec): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(spec))
  } catch {
    // localStorage full / disabled — silently drop, the chart still works in-memory.
  }
}

/** A small state wrapper that mirrors `useState`'s shape but writes through to localStorage on
 *  every update. Reads happen via the initial value (caller does the `loadSpec` itself so the
 *  initial render is hydrated). When `storageKey` changes (operator switches tabs / connectors),
 *  the next stored spec is rehydrated. */
function useStoredSpec(storageKey: string, initial: ChartSpec): [ChartSpec, (next: ChartSpec) => void] {
  const [spec, setSpec] = useState<ChartSpec>(initial)
  useEffect(() => {
    const loaded = loadSpec(storageKey)
    if (loaded) setSpec(loaded)
    // No `else` — falling back to whatever the caller seeded keeps the chart populated when
    // a new connector/query has no saved spec yet.
  }, [storageKey])
  return [spec, (next) => { setSpec(next); saveSpec(storageKey, next) }]
}

/** Seed: first non-hidden column is X, first numeric column is Y. Falls back to the first
 *  column for Y when nothing parses as numeric (count aggregation will still produce a chart). */
function seedSpec(allCols: Column[], numericCols: Column[]): ChartSpec {
  const base = defaultChartSpec()
  const x = allCols[0]?.name ?? ''
  const y = (numericCols[0] ?? allCols[1] ?? allCols[0])?.name
  return { ...base, x, y: y ? [y] : [] }
}

// ── series editor helpers ───────────────────────────────────────────────────────────
//
// Each helper does ONE mutation of the (y, colors, yAxis) trio. We treat the three arrays
// as a single logical "series list" — colours and axes are positional so a change in y[i]
// must keep colors[i] / yAxis[i] aligned. Trailing-default trimming happens at save time
// (see toSavedSpec); here we keep arrays at-or-shorter than y to avoid the validator error.

function updateSeriesColumn(spec: ChartSpec, index: number, name: string): ChartSpec {
  const y = [...spec.y]
  y[index] = name
  return { ...spec, y }
}

function updateSeriesColor(spec: ChartSpec, index: number, color: string): ChartSpec {
  // Pad with empty strings up to ``index`` so positional indexing stays correct when an
  // operator skips ahead (colours series 3 first); the empty entries fall back to the
  // palette in seriesColorAt.
  const colors = [...(spec.colors ?? [])]
  while (colors.length <= index) colors.push('')
  colors[index] = color
  return { ...spec, colors }
}

function updateSeriesAxis(spec: ChartSpec, index: number, side: YAxisSide): ChartSpec {
  const yAxis = [...(spec.yAxis ?? [])]
  while (yAxis.length <= index) yAxis.push('left')
  yAxis[index] = side
  return { ...spec, yAxis }
}

function removeSeries(spec: ChartSpec, index: number): ChartSpec {
  // Drop the parallel index from all three arrays so the remaining series keep their
  // colour + axis assignments (rather than shifting onto the next operator's choices).
  const y = spec.y.filter((_, i) => i !== index)
  const colors = (spec.colors ?? []).filter((_, i) => i !== index)
  const yAxis = (spec.yAxis ?? []).filter((_, i) => i !== index)
  return { ...spec, y, colors, yAxis }
}

function addSeries(spec: ChartSpec, allCols: Column[]): ChartSpec {
  // Pick the first column not already in ``y`` — operators almost always want a NEW
  // series (a duplicate would render as an invisible overlay). Falls back to the first
  // column if every one is already plotted (degenerate but valid).
  const used = new Set(spec.y)
  const next = allCols.find((c) => !used.has(c.name)) ?? allCols[0]
  if (!next) return spec
  return { ...spec, y: [...spec.y, next.name] }
}

function hasUnusedColumn(y: string[], allCols: Column[]): boolean {
  const used = new Set(y)
  return allCols.some((c) => !used.has(c.name))
}

/** ``<input type="color">`` requires a 7-char lowercase hex; the palette uses theme tokens
 *  that may not be hex (rgb(), oklch(), CSS vars). Normalise: if the value is already a hex,
 *  pass it through; otherwise resolve via a hidden canvas — a one-off computation that picks
 *  up the rendered colour from the browser. Falls back to neutral grey when the resolution
 *  fails (the swatch still shows the original colour; only the picker dialog initialises grey). */
function normaliseHex(color: string): string {
  if (/^#[0-9a-f]{6}$/i.test(color)) return color.toLowerCase()
  try {
    // Use a 1×1 canvas to coerce any browser-accepted colour string into rgb(...), then
    // pack the channels into the 7-char hex the colour input expects.
    const ctx = document.createElement('canvas').getContext('2d')
    if (!ctx) return '#888888'
    ctx.fillStyle = color
    const rgb = ctx.fillStyle as string             // canvas normalises to "#rrggbb" or "rgba(...)"
    if (/^#[0-9a-f]{6}$/i.test(rgb)) return rgb.toLowerCase()
    const match = rgb.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/)
    if (!match) return '#888888'
    const hex = (n: string) => Number(n).toString(16).padStart(2, '0')
    return `#${hex(match[1])}${hex(match[2])}${hex(match[3])}`
  } catch {
    return '#888888'
  }
}
