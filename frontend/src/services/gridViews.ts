// Per-user saved grid views — durable replacement for the `dt-*` localStorage that
// used to hold a table's column layout. Backed by GET/PUT/DELETE /api/views/grid
// (liberty/web/views.py), keyed by the DataTable's app-scoped `tableId`
// (e.g. `screen:<app>:<id>` or `sql:<connector>:<query>`), so saved views follow
// the user across devices and two apps with the same table never collide.
//
// A user can save MANY named views per key ("My views"); the shared/default views
// live in the screen config (server-rendered), not here.
import { api } from '../api/client'
import type {
  ColumnFiltersState, ColumnOrderState, GroupingState, SortingState, VisibilityState,
} from '@tanstack/react-table'

/** The persisted grid format. All fields optional — an older/narrower payload still applies. */
export interface GridView {
  visibility?: VisibilityState
  order?: ColumnOrderState
  sorting?: SortingState
  filters?: ColumnFiltersState
  grouping?: GroupingState
  pageSize?: number
}

/** A user's named saved view for a key. */
export interface NamedGridView {
  name: string
  payload: GridView
  updated_at?: string | null
}

const KIND = 'grid'

/** The caller's named views for *key* (newest schema first by name). Empty on error. */
export async function listGridViews(key: string): Promise<NamedGridView[]> {
  try {
    const r = await api.get<{ views: NamedGridView[] }>(
      `/api/views/${KIND}?key=${encodeURIComponent(key)}`,
    )
    return r.views ?? []
  } catch {
    return [] // no auth / endpoint down → behave as "no saved views"
  }
}

/** Upsert one of the caller's named views for *key*. */
export async function saveGridView(key: string, name: string, view: GridView): Promise<void> {
  await api.put(`/api/views/${KIND}`, { key, name, payload: view })
}

/** Delete one of the caller's named views. */
export async function deleteGridView(key: string, name: string): Promise<void> {
  try {
    await api.del(`/api/views/${KIND}?key=${encodeURIComponent(key)}&name=${encodeURIComponent(name)}`)
  } catch {
    /* already gone / offline — the in-memory state still updates */
  }
}

// ── shared views (from the screen config) ──────────────────────────────────────
/** A shared, read-only grid view authored in the Screen editor — available to all users.
 *  Column names match the result column ids (resolved case-insensitively). */
export interface SharedGridView {
  name: string
  default?: boolean
  columns?: string[]                          // visible columns, in display order
  sort?: { id: string; desc?: boolean }[]
  grouping?: string[]
  pageSize?: number
}

/** Resolve a column name to its actual id, case-insensitively (Postgres folds identifiers). */
function resolveCol(name: string, byLower: Map<string, string>): string {
  return byLower.get(name.toLowerCase()) ?? name
}

/** Convert a shared view's column-name spec into an applicable `GridView`, using the grid's
 *  actual data column ids (in screen order) to compute visibility for the omitted columns. */
export function sharedToGridView(sv: SharedGridView, allColumnIds: string[]): GridView {
  const byLower = new Map(allColumnIds.map((id) => [id.toLowerCase(), id] as const))
  const out: GridView = {}
  if (sv.columns?.length) {
    const visible = sv.columns.map((c) => resolveCol(c, byLower))
    const visSet = new Set(visible)
    out.order = visible
    const vis: VisibilityState = {}
    for (const id of allColumnIds) vis[id] = visSet.has(id)
    out.visibility = vis
  }
  if (sv.sort?.length) out.sorting = sv.sort.map((s) => ({ id: resolveCol(s.id, byLower), desc: !!s.desc }))
  if (sv.grouping?.length) out.grouping = sv.grouping.map((g) => resolveCol(g, byLower))
  if (typeof sv.pageSize === 'number') out.pageSize = sv.pageSize
  return out
}

// ── last-opened pointer (per device) ───────────────────────────────────────────
// localStorage keeps ONLY which view this device last opened for a key — the heavy view
// content lives in the shared screen config + the per-user DB rows. A returning user lands
// back on their last view.
export interface ViewRef { scope: 'shared' | 'user'; name: string }

const lastKey = (key: string) => `liberty:grid-lastview:${key}`

export function loadLastView(key: string): ViewRef | null {
  try {
    const raw = localStorage.getItem(lastKey(key))
    if (!raw) return null
    const v = JSON.parse(raw)
    return v && (v.scope === 'shared' || v.scope === 'user') && typeof v.name === 'string' ? v : null
  } catch { return null }
}

export function saveLastView(key: string, ref: ViewRef | null): void {
  try {
    if (ref) localStorage.setItem(lastKey(key), JSON.stringify(ref))
    else localStorage.removeItem(lastKey(key))
  } catch { /* storage disabled — last-view memory is best-effort */ }
}
