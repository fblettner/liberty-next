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
