// A job's "load a file" step, as its form reads it: where the file comes from (the step stores a
// path, a watched folder, or neither), the columns of the table it fills, the queries it may be
// refused by. Pure, except the one lookup of a table's columns.
import { getPoolSchemaTables } from './poolSchema'

/** `upload` = given by who starts the job; `path` = a file on the server; `folder` = a watched folder. */
export type FileSource = 'upload' | 'path' | 'folder'

export interface FileLoadFields {
  path?: string | null
  watch_dir?: string | null
  watch_pattern?: string | null
}

const filled = (v: unknown): boolean => typeof v === 'string' && v.trim() !== ''

/** What the step's fields say. The server takes an uploaded file first, then the path, then the
 *  folder: a step with a path AND a folder reads the path. */
export function fileSourceOf(step: FileLoadFields): FileSource {
  if (filled(step.path)) return 'path'
  if (filled(step.watch_dir)) return 'folder'
  return 'upload'
}

/** The fields to write when the source is chosen: what belongs to another source is emptied, so
 *  the step never keeps a path that would win over the folder just picked. */
export function fileSourcePatch(source: FileSource, step: FileLoadFields): FileLoadFields {
  // no pattern written unless a folder is watched with one of its own: "*" is the server's default
  if (source === 'path') return { path: step.path ?? '', watch_dir: undefined, watch_pattern: undefined }
  if (source === 'folder') return { path: undefined, watch_dir: step.watch_dir ?? '', watch_pattern: step.watch_pattern || undefined }
  return { path: undefined, watch_dir: undefined, watch_pattern: undefined }
}

/** The step as its flow card names it: the file, the folder, or "given at start". */
export function fileSourceSummary(step: FileLoadFields): string {
  const source = fileSourceOf(step)
  if (source === 'path') return String(step.path).split('/').pop() || String(step.path)
  if (source === 'folder') return `${String(step.watch_dir).replace(/\/+$/, '')}/${step.watch_pattern || '*'}`
  return ''
}

/** The column names of one table (`[]` when it cannot be read: unknown table, no right, no database). */
export async function tableColumns(connector: string, schema: string, table: string): Promise<string[]> {
  const name = table.trim()
  if (!connector || !name) return []
  const found = await getPoolSchemaTables(connector, schema.trim() || null, name).catch(() => null)
  const hit = (found?.tables ?? []).find((t) => t.name.toLowerCase() === name.toLowerCase())
  return (hit?.columns ?? []).map((c) => c.name)
}
