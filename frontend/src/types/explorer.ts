// The Data Explorer's wire shapes (GET /api/explorer/{app}/model, POST …/query, …/detail,
// …/calc/test) and the one AnalysisDefinition the page edits, saves and runs.
import type { DisplayRule } from './connectors'
import type { DatePreset, DateRange } from '../services/period'

export type FieldType = 'string' | 'number' | 'date' | 'datetime' | 'boolean'
export type Aggregate = 'sum' | 'avg' | 'min' | 'max' | 'count' | 'count_distinct'
export type Bucket = 'day' | 'week' | 'month' | 'quarter' | 'year'
export type Cardinality = 'one' | 'many'

export interface ExplorerField {
  key: string                // entity.NAME (or calc.NAME)
  entity: string
  name: string
  label: string
  type: FieldType
  kind: 'dimension' | 'measure'
  aggregate?: Aggregate
  format?: string
  decimals?: number
  rule?: DisplayRule
  dd?: string
  hidden?: boolean
}

export interface ExplorerEntity {
  id: string
  label: string
  description?: string | null
  connector?: string | null
  query: string
  screen?: string | null
  key: string[]
  cardinality: Cardinality
  default_date?: string | null
  fields: ExplorerField[]
  error?: string
}

export interface ExplorerModel {
  app: string
  model: string
  label: string
  description?: string | null
  hub: string
  entities: ExplorerEntity[]
  relations: { from: string; to: string; on: string[][] }[]
  warnings: string[]
  /** The preset the model was filled from, when any. */
  preset?: string | null
  /** Not in explorer.toml: the model a preset flagged `explorer = true` ships by default. */
  derived?: boolean
  /** Visible columns no dictionary entry documents (shown by their column name). */
  undocumented?: number
}

export interface ExplorerModelSummary { app: string; model: string; label: string; description?: string | null; hub: string; entities: number; preset?: string | null; derived?: boolean }

// ── the analysis definition ──────────────────────────────────────────────────

export type FilterOp = 'contains' | 'equals' | 'notEquals' | 'startsWith' | 'endsWith' | 'in' | 'empty' | 'notEmpty' | 'lt' | 'le' | 'gt' | 'ge' | 'between'

export interface FilterRow { field: string; op: FilterOp; value: string; valueTo?: string }
export interface DimRow { field: string; bucket?: Bucket | null }
export interface MeasureRow { field: string | null; agg: Aggregate }   // field null = count(*)
export interface CalcDef { name: string; label?: string; expr: string }
export interface PeriodDef { field: string; preset: DatePreset; range: DateRange }
export type AnalysisView = 'table' | 'pivot' | 'detail' | 'bar' | 'line' | 'pie' | 'kpi'

export interface AnalysisDefinition {
  entity: string
  view: AnalysisView
  rows: DimRow[]
  measures: MeasureRow[]
  filterRows: FilterRow[]
  period: PeriodDef | null
  sort: { key: string; dir: 'asc' | 'desc' }[]
  limit: number
  calculated: CalcDef[]
  pivotColumn?: string | null
  pivot?: { measure?: string | null; percent?: 'none' | 'parent' | 'total' }
  chart?: { measure?: string | null; horizontal?: boolean; stacked?: boolean; area?: boolean; donut?: boolean }
}

// ── results ──────────────────────────────────────────────────────────────────

export interface ResultColumn {
  key: string
  kind: 'dimension' | 'measure' | 'column'
  field: string | null
  entity: string
  label: string
  type: FieldType
  bucket?: Bucket
  aggregate?: Aggregate
  format?: string
  decimals?: number
  rule?: DisplayRule
}

export interface AggregateResult {
  mode: 'aggregate'
  entity: string
  columns: ResultColumn[]
  rows: Record<string, unknown>[]
  groups: number
  scanned: number
  truncated: boolean
  scan_truncated: boolean
  sql?: string
  binds?: Record<string, unknown>
}

export interface DetailResult {
  mode: 'detail'
  entity: string
  columns: ResultColumn[]
  rows: Record<string, unknown>[]
  total: number
  page: number
  page_size: number
  sql?: string
}

export interface CalcTestResult { name: string; type: FieldType; kind: 'dimension' | 'measure'; values: unknown[]; sql?: string }

export interface DrillCell { field: string; bucket?: Bucket | null; value: unknown }
