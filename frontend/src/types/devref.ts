// /admin/dev — the Developer page (C12).

export interface ConfigReader { file: string; line: number; snippet: string; via: string | null }

export interface ConfigKey {
  key: string
  section: string
  name: string
  type: string
  default: unknown
  description: string | null
  /** The screen that edits it, or null. */
  editor: string | null
  /** Why it stays in the file / the environment, or null. */
  file_only: string | null
  readers: ConfigReader[]
}

export interface ConfigReference { keys: ConfigKey[]; gaps: string[]; editable: number; total: number }

export interface ApiRoute {
  method: string
  path: string
  summary: string
  description: string
  /** The module (``jobs``, ``settings · screens``…) and its category (Access, Workspace…). */
  group: string
  category: string
  tag: string
  /** public · signed-in · superuser · permission · role */
  access: string
  /** The permission patterns it checks (``sql:{connector}:{query}``, ``ai:chat``…). */
  permissions: string[]
  file: string
  line: number
  in_schema: boolean
  params: string[]
  /** From the OpenAPI schema FastAPI builds from the route's models. */
  parameters: { name: string; in: string; type: string; required: boolean; description: string }[]
  request: { type: string; content: string; fields: { name: string; type: string; required: boolean; description: string }[]; example: unknown } | null
  /** null when the route returns JSON it builds itself (no declared response model). */
  response: { code: string; type: string; example: unknown } | null
}

export interface ApiReference { routes: ApiRoute[]; total: number }

export interface DataColumn {
  name: string
  type: string
  label: string
  key: boolean
  nullable: boolean
  /** ``table.column`` it references (framework tables' foreign keys). */
  references: string | null
  /** The screens that show it (connector tables). */
  screens: string[]
  description: string
}

export interface DataTableRef {
  id: string
  name: string
  /** framework · connector */
  owner: string
  /** The framework area (nomaflow, accounts…) or the connector. */
  group: string
  label: string
  description: string
  physical: string
  physical_raw: string
  key: string[]
  related: string[]
  /** The CRUD queries a connector table declares (get / put / post / delete). */
  slots: string[]
  used_by: { kind: string; name: string }[]
  columns: DataColumn[]
}

export interface DataModel { tables: DataTableRef[]; total: number }

/** A configuration entity of the Impact view (C12) — ``(kind, scope, name)`` as the dependency walker
 *  names it; ``missing`` = referenced but not defined; ``link`` = its editor (find-usages' deep link). */
export interface ImpactNode {
  kind: string
  scope: string | null
  name: string
  /** Its label (in the request's language when translated) — a screen without one takes its menu item's. */
  label: string | null
  description: string | null
  missing: boolean
  link: Record<string, unknown>
}

export interface ImpactEntity extends ImpactNode { uses: number; used_by: number; unused: boolean }

export interface ImpactCatalogue { entities: ImpactEntity[]; total: number; kinds: string[]; broken: ImpactNode[] }

export interface ImpactUse extends ImpactNode { reason: string }
/** A referrer in the used-by tree: ``parent`` = the index of the node it points at (-1 = the entity). */
export interface ImpactReferrer extends ImpactNode { depth: number; parent: number; reason: string; fan_in: number }

export interface ImpactDetail {
  entity: ImpactNode & { uses: number; used_by: number }
  uses: ImpactUse[]
  closure: (ImpactNode & { depth: number })[]
  closure_by_kind: Record<string, number>
  used_by: ImpactReferrer[]
  reach_by_kind: Record<string, number>
  truncated: boolean
}
