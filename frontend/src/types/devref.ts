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
