// Shapes of the structured-config admin endpoints (liberty/web/admin.py — the Phase-7 builders).
import type { FrameworkEnums, JsonSchema } from '../common/SchemaForm'

/** GET /admin/config/schema — JSON Schema of the config models the builder forms render from
 *  (`sql` / `api` carry their own `$defs` for QueryDef / ColumnHint / ParamDef / EndpointDef),
 *  plus `framework_enums` — v2's port of v1's `ly_enum`-for-the-framework registry, threaded into
 *  the form via FrameworkEnumsContext so fields with `x_enum_ref` render as themed dropdowns. */
export interface ConfigSchemas {
  pool: JsonSchema
  sql: JsonSchema
  api: JsonSchema
  dictionary: JsonSchema
  framework_enums: FrameworkEnums
}

/** GET /admin/config/pools — the current `[pools.*]` as `{name: PoolConfig dict}`. */
export interface PoolsDoc {
  path: string
  pools: Record<string, Record<string, unknown>>
}

/** GET /admin/config/connectors/parsed — the current `[connectors.*]` (default-valued keys dropped). */
export interface ConnectorsDoc {
  path: string
  connectors: Record<string, Record<string, unknown>>
}

/** One section of the shared dictionary (top-level OR nested under `connectors.<name>`). */
export interface DictionarySection {
  entries?: Record<string, Record<string, unknown>>
  enums?: Record<string, Record<string, unknown>>
  lookups?: Record<string, Record<string, unknown>>
}

/** GET /admin/config/dictionary/parsed — the current `dictionary.toml`, default keys dropped.
 *  `framework_enums` is top-level only (no per-connector overlay) — it overrides the bundled
 *  framework registry that drives the builder dropdowns. */
export interface DictionaryDoc {
  path: string
  dictionary: DictionarySection & {
    default_language?: string
    connectors?: Record<string, DictionarySection>
    framework_enums?: Record<string, Record<string, unknown>>
  }
}

/** Which kind of dictionary record the builder is editing. `framework_enums` is the operator
 *  override for the bundled `liberty/framework_enums.py` registry — shared scope only. */
export type DictionaryKind = 'entries' | 'enums' | 'lookups' | 'framework_enums'
