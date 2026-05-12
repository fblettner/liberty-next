// Shapes of the structured-config admin endpoints (liberty/web/admin.py — the Phase-7 builders).
import type { JsonSchema } from '../common/SchemaForm'

/** GET /admin/config/schema — JSON Schema of the config models the builder forms render from. */
export interface ConfigSchemas {
  pool: JsonSchema
  [section: string]: JsonSchema
}

/** GET /admin/config/pools — the current `[pools.*]` as `{name: PoolConfig dict}`. */
export interface PoolsDoc {
  path: string
  pools: Record<string, Record<string, unknown>>
}
