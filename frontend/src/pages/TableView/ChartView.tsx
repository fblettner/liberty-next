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
import { Save } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { Button, Field, SearchSelect } from '../../common'
import { isNumericColumn } from '../../services/chartData'
import type { Column, QueryResult } from '../../types/connectors'
import type { ChartSpec, SavedChartSpec } from '../../types/charts'
import { defaultChartSpec, fromSavedSpec } from '../../types/charts'
import { ChartCanvas } from './ChartCanvas'
import { ChartSpecEditor } from './ChartSpecEditor'
import { SaveChartModal } from './SaveChartModal'

const Frame = styled.div`
  display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 12px;
`
const SpecCell = styled.div`min-width: 140px;`

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
      {/* The shared visual spec bar (type / X / series / aggregate). The Load picker + Save
          button are TableView-specific, so they ride in the `trailing` slot — they sit flush
          right at the end of the bar exactly as before. */}
      <ChartSpecEditor
        result={result}
        spec={cleanSpec}
        onChange={setSpec}
        trailing={<>
          {/* Load picker — surfaces saved charts that target THIS (connector, query). Hidden
              when nothing matches (empty charts.toml or no matches for this query). */}
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
                  options={matchingSavedCharts.map((c) => ({ value: c.id, label: c.label, mono: c.id }))}
                  placeholder={t('chart.load.placeholder', 'Apply a saved chart…')}
                />
              </Field>
            </SpecCell>
          )}
          {canSave && (
            <div style={{ marginLeft: matchingSavedCharts.length > 0 ? undefined : 'auto', alignSelf: 'flex-end' }}>
              <Button $size="sm" $variant="ghost" onClick={() => setSaveOpen(true)} disabled={!canSubmit}
                title={canSubmit ? t('chart.save.buttonTitle') : t('chart.save.buttonDisabled')}>
                <Save size={13} /> {t('chart.save.button')}
              </Button>
            </div>
          )}
        </>}
      />

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

