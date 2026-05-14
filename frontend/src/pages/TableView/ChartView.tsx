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
import { Button, Field, SearchSelect, type SearchSelectOption } from '../../common'
import { isNumericColumn } from '../../services/chartData'
import type { Column, QueryResult } from '../../types/connectors'
import type { Aggregation, ChartSpec, ChartType } from '../../types/charts'
import { AGGREGATIONS, CHART_TYPES, defaultChartSpec } from '../../types/charts'
import { ChartCanvas } from './ChartCanvas'
import { SaveChartModal } from './SaveChartModal'

const Frame = styled.div`
  display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 12px;
`
const SpecBar = styled.div`
  display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end;
`
const SpecCell = styled.div`min-width: 140px;`

const STORAGE_PREFIX = 'liberty:chart-spec:'

export interface ChartViewProps {
  result: QueryResult
  connector: string
  query: string
}

export function ChartView({ result, connector, query }: ChartViewProps) {
  const { t } = useTranslation()
  const allCols = useMemo(() => result.columns.filter((c) => !c.hidden), [result])
  const numericCols = useMemo(() => allCols.filter(isNumericColumn), [allCols])

  // Persist the spec per `(connector, query)` — same shape `config/charts.toml` will adopt.
  const storageKey = `${STORAGE_PREFIX}${connector}/${query}`
  const initialSpec = useMemo(() => loadSpec(storageKey) ?? seedSpec(allCols, numericCols), [storageKey, allCols, numericCols])
  // Component state held in localStorage indirectly: a `setSpec` writes through.
  // (A plain `useState(initialSpec)` with `useEffect` to write would also work; this is terser.)
  const [spec, setSpec] = useStoredSpec(storageKey, initialSpec)

  // Repair stale Y columns (someone changed the query → an old Y col no longer exists). Strip
  // missing ones; if all gone, reseed from the new result.
  const cleanSpec: ChartSpec = useMemo(() => {
    const colNames = new Set(allCols.map((c) => c.name))
    const y = spec.y.filter((n) => colNames.has(n))
    const x = colNames.has(spec.x) ? spec.x : (allCols[0]?.name ?? '')
    if (y.length === 0 && numericCols.length > 0) {
      return { ...spec, x, y: [numericCols[0].name] }
    }
    return { ...spec, x, y }
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
        <SpecCell>
          <Field label={t('chart.spec.y')}>
            <SearchSelect
              value={cleanSpec.y[0] ?? ''}
              onChange={(v) => setSpec({ ...cleanSpec, y: v ? [v, ...cleanSpec.y.slice(1)] : [] })}
              options={colOpts} placeholder={t('chart.spec.pick')}
            />
          </Field>
        </SpecCell>
        <SpecCell>
          <Field label={t('chart.spec.aggregation')}>
            <SearchSelect value={cleanSpec.aggregation} onChange={(v) => setSpec({ ...cleanSpec, aggregation: v as Aggregation })} options={aggOpts} />
          </Field>
        </SpecCell>
        {canSave && (
          // Pushed to the right end of the spec bar (`margin-left: auto`) so the four-dropdown
          // group stays visually grouped at the start. Disabled when there's nothing to save
          // (no X or no Y column picked yet).
          <div style={{ marginLeft: 'auto', alignSelf: 'flex-end' }}>
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
          onSaved={() => setSaveOpen(false)}
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
