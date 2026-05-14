// App navigation menu shapes returned by GET /api/menus — already resolved (labels in the
// request's language) and pruned to what the caller may run. A folder node carries `items`;
// a leaf carries `type`/`connector`/`target` (and `params` when the menu pins some).

export interface MenuNode {
  id: string
  label: string
  icon?: string // a lucide icon name (a hint; the UI may or may not use it)
  // folder:
  items?: MenuNode[]
  // leaf:
  type?: 'query' | 'endpoint' | 'dashboard'
  /** Connector name. Absent on `dashboard` leaves (dashboards have a flat namespace, no
   *  connector); always present on `query` / `endpoint` leaves. */
  connector?: string
  /** The query / endpoint name (sql / api) or the dashboard id (dashboard). */
  target?: string
  params?: Record<string, unknown>
}

export interface AppMenuTree {
  app: string
  label: string
  items: MenuNode[]
}

export type MenusByApp = Record<string, AppMenuTree>
