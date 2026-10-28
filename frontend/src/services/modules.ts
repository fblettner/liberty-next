// Module catalogues in the Screen Assistant: the routes and the plain logic of the "A module" view
// (grouping, what a plan would write, the screen tree). No React here.
import { api } from '../api/client'
import type { ModuleApplyResult, ModulePlan, ModuleSummary } from '../types/modules'

export const getModules = (): Promise<ModuleSummary[]> =>
  api.get<{ modules: ModuleSummary[] }>('/admin/assistant/modules').then((r) => r.modules)

const q = (replace: boolean) => (replace ? '?replace=1' : '')
export const previewModule = (id: string, replace: boolean): Promise<ModulePlan> =>
  api.post<ModulePlan>(`/admin/assistant/modules/${encodeURIComponent(id)}/preview${q(replace)}`)
export const applyModule = (id: string, replace: boolean): Promise<ModuleApplyResult> =>
  api.post<ModuleApplyResult>(`/admin/assistant/modules/${encodeURIComponent(id)}/apply${q(replace)}`)

/** Modules by group (``group`` or "Other"), groups and modules in label order, narrowed by *query*
 *  (label, description, a table or a screen label). */
export function groupModules(modules: ModuleSummary[], query = '', other = 'Other'): [string, ModuleSummary[]][] {
  const needle = query.trim().toLowerCase()
  const hit = (m: ModuleSummary) => !needle || [m.label, m.description ?? '', ...m.screens.flatMap((s) => [s.table, s.label])]
    .some((x) => x.toLowerCase().includes(needle))
  const by = new Map<string, ModuleSummary[]>()
  for (const m of modules.filter(hit)) by.set(m.group || other, [...(by.get(m.group || other) ?? []), m])
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([g, list]) => [g, list.sort((a, b) => a.label.localeCompare(b.label))])
}

/** How many entries the plan writes (new + replaced) — nothing to apply when 0. */
export function writes(plan: ModulePlan): number {
  return plan.new.queries.length + plan.new.screens.length + plan.new.menu_items.length
}

/** The module's screens as a tree: the screens no other one lists as a detail at the top, each with
 *  its details under it (depth first), for the preview's indented list. */
export function screenTree(module: ModuleSummary): { id: string; depth: number }[] {
  const children = new Set(module.screens.flatMap((s) => s.details))
  const by = new Map(module.screens.map((s) => [s.id, s]))
  const out: { id: string; depth: number }[] = []
  const walk = (id: string, depth: number, seen: Set<string>) => {
    if (seen.has(id)) return
    out.push({ id, depth })
    for (const d of by.get(id)?.details ?? []) walk(d, depth + 1, new Set([...seen, id]))
  }
  for (const s of module.screens) if (!children.has(s.id)) walk(s.id, 0, new Set())
  return out
}
