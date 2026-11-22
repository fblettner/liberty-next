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

/** A saved analysis or dashboard with the model it was saved in — what "find it wherever it is" lists. */
export interface FoundSaved {
  app: string
  model: string
  model_label: string
  kind: SavedKind
  id: string
  name: string
  description?: string | null
  audience?: string | null
  owner?: string | null
  shared: boolean
  updated_at?: string | null
}

/** Every saved analysis and dashboard of the models the caller may use, whatever the model. */
export async function findSaved(): Promise<FoundSaved[]> {
  const r = await api.get<{ items: FoundSaved[] }>('/api/explorer/saved')
  return r.items
}

/** How many analyses and dashboards each model holds, by `app/model` — what the model picker shows. */
export function savedCounts(found: readonly FoundSaved[]): Record<string, { analyses: number; dashboards: number }> {
  const out: Record<string, { analyses: number; dashboards: number }> = {}
  for (const f of found) {
    const at = (out[`${f.app}/${f.model}`] ??= { analyses: 0, dashboards: 0 })
    at[f.kind] += 1
  }
  return out
}

const plain = (s: string): string => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()

/** The saved objects that say every word typed — in their name, description, audience, owner or model
 *  (case and accents ignored) — of one kind or of both. */
export function matchSaved(found: readonly FoundSaved[], text: string, kind: SavedKind | 'all' = 'all'): FoundSaved[] {
  const words = plain(text).split(/\s+/).filter(Boolean)
  return found.filter((f) => {
    if (kind !== 'all' && f.kind !== kind) return false
    const hay = plain([f.name, f.description, f.audience, f.owner, f.model_label, f.model, f.app].filter(Boolean).join(' '))
    return words.every((w) => hay.includes(w))
  })
}

/** The found objects under their model, the models in the order given (the server sorts them by application). */
export function groupFound(found: readonly FoundSaved[]): { key: string; app: string; model: string; label: string; items: FoundSaved[] }[] {
  const groups: { key: string; app: string; model: string; label: string; items: FoundSaved[] }[] = []
  for (const f of found) {
    const key = `${f.app}/${f.model}`
    let g = groups.find((x) => x.key === key)
    if (!g) { g = { key, app: f.app, model: f.model, label: f.model_label, items: [] }; groups.push(g) }
    g.items.push(f)
  }
  return groups
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
