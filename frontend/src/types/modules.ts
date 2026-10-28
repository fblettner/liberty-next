// Module catalogues (catalogue v2) — the shapes of /admin/assistant/modules (liberty/web/admin.py,
// liberty/module_apply.py).

export interface ModuleSummary {
  id: string
  label: string
  l?: Record<string, string>
  description?: string | null
  group?: string | null
  connector: string
  app: string
  screens: { id: string; table: string; label: string; details: string[] }[]
}

export type EntryStatus = 'new' | 'exists' | 'replace'

export interface ModuleScreenPlan {
  id: string
  table: string
  prefix: string | null
  status: EntryStatus
  query: string
  query_status: EntryStatus
  columns: number
  missing: string[]
  error: string | null
}

export interface ModulePlan {
  module: string
  connector: string
  app: string
  screens: ModuleScreenPlan[]
  menu: { folder: EntryStatus; items: { id: string; status: EntryStatus }[] }
  errors: string[]
  replace: boolean
  new: { queries: string[]; screens: string[]; menu_items: string[] }
}

export interface ModuleApplyResult extends ModulePlan {
  dictionary: { created: string[]; scanned: number; skipped: string[] }
}
