// Per-user saved grid views — durable replacement for the `dt-*` localStorage that
// used to hold a table's column layout. Backed by GET/PUT/DELETE /api/views/grid
// (liberty/web/views.py), keyed by the DataTable's app-scoped `tableId`
// (e.g. `screen:<app>:<id>` or `sql:<connector>:<query>`), so a saved view follows
// the user across devices and two apps with the same table never collide.
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

const KIND = 'grid'

/** The caller's saved view for *key*, or null when none is saved (→ screen default / columns). */
export async function loadGridView(key: string): Promise<GridView | null> {
  try {
    const r = await api.get<{ payload: GridView | null }>(
      `/api/views/${KIND}?key=${encodeURIComponent(key)}`,
    )
    return r.payload ?? null
  } catch {
    return null // no auth / endpoint down → behave as "no saved view" (falls back to defaults)
  }
}

/** Upsert the caller's view for *key*. */
export async function saveGridView(key: string, view: GridView): Promise<void> {
  await api.put(`/api/views/${KIND}`, { key, payload: view })
}

/** Delete the caller's saved view (reset to the screen default / column config). */
export async function resetGridView(key: string): Promise<void> {
  try {
    await api.del(`/api/views/${KIND}?key=${encodeURIComponent(key)}`)
  } catch {
    /* already gone / offline — the in-memory reset still happens */
  }
}
