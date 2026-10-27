// Saved analyses and dashboards of analyses — the /api/explorer/{app}/saved/{kind} routes and
// the dashboard definition shape. No React.
import { api } from '../api/client'
import { postDownload } from './download'
import type { AnalysisDefinition } from '../types/explorer'
import type { DatePreset, DateRange } from './period'

export type SavedKind = 'analyses' | 'dashboards'

export interface SavedItem<D = unknown> {
  id: string
  name: string
  description?: string | null
  audience?: string | null
  owner: string
  shared: boolean
  bundled?: number | null
  version: number
  updated_at?: string | null
  definition: D
}

export type SavedAnalysis = SavedItem<AnalysisDefinition>

export type TileHeight = 's' | 'm' | 'l'
export interface DashboardTile { analysis: string; w: 1 | 2 | 3 | 4; h: TileHeight; title?: string }
export interface DashboardDefinition { period?: { preset: DatePreset; range: DateRange } | null; tiles: DashboardTile[] }
export type SavedDashboard = SavedItem<DashboardDefinition>

export const TILE_HEIGHT: Record<TileHeight, number> = { s: 240, m: 340, l: 500 }

export async function listSaved<D>(app: string, model: string, kind: SavedKind): Promise<SavedItem<D>[]> {
  const r = await api.get<{ items: SavedItem<D>[] }>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(model)}/saved/${kind}`)
  return r.items
}

export async function saveSaved<D>(app: string, model: string, kind: SavedKind, body: {
  id?: string | null; name: string; description?: string | null; audience?: string | null; shared: boolean; as_new?: boolean; definition: D
}): Promise<SavedItem<D>> {
  const r = await api.put<{ item: SavedItem<D> }>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(model)}/saved/${kind}`, body)
  return r.item
}

export async function deleteSaved(app: string, model: string, kind: SavedKind, id: string): Promise<boolean> {
  const r = await api.del<{ deleted: boolean }>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(model)}/saved/${kind}/${encodeURIComponent(id)}`)
  return r.deleted
}

/** Picker options grouped by audience: "audience · name" with the id in mono. */
export function savedOptions<D>(items: SavedItem<D>[], mineLabel: string): { value: string; label: string; mono?: string }[] {
  return items.map((i) => ({ value: i.id, label: `${i.audience ? `${i.audience} · ` : ''}${i.name}${i.shared ? '' : ` (${mineLabel})`}`, mono: i.id }))
}

/** A dashboard period applies to an analysis only when that analysis has a period field. */
export function withDashboardPeriod(def: AnalysisDefinition, period: DashboardDefinition['period']): AnalysisDefinition {
  if (!period || !def.period?.field) return def
  return { ...def, period: { field: def.period.field, preset: period.preset, range: period.range } }
}

/** Download an export: an inline definition, a saved analysis or a saved dashboard, as xlsx or pdf. */
export function downloadExport(app: string, model: string, body: { format: 'xlsx' | 'pdf'; definition?: AnalysisDefinition; analysis?: string; dashboard?: string; title?: string }): Promise<void> {
  return postDownload(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(model)}/export`, body, `export.${body.format}`)
}
