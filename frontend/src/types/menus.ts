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
  type?: 'query' | 'endpoint'
  connector?: string
  target?: string
  params?: Record<string, unknown>
}

export interface AppMenuTree {
  app: string
  label: string
  items: MenuNode[]
}

export type MenusByApp = Record<string, AppMenuTree>
