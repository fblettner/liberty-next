// The Server log page's calls and plain helpers (no React): the search, the export / download,
// the levels per module.
import { api, authHeaders } from '../api/client'

export interface LogEntry { ts: string; level: string; logger: string; msg: string; request_id?: string; exc?: string }
export interface LogSearch {
  entries: LogEntry[]; scanned: number; truncated: boolean; modules: string[]
  files: { name: string; size: number }[]; path: string
}
export interface LogFilters { level: string; module: string; q: string; hours: string }
export interface LogLevels { levels: Record<string, string>; configured: Record<string, string>; loggers: string[]; choices: string[] }

/** The query string of a search (blank filters left out). */
export function logQuery(f: LogFilters, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams()
  if (f.level) p.set('level', f.level)
  if (f.module.trim()) p.set('module', f.module.trim())
  if (f.q.trim()) p.set('q', f.q.trim())
  p.set('hours', f.hours || '0')
  for (const [k, v] of Object.entries(extra)) p.set(k, v)
  return p.toString()
}

export const searchLog = (f: LogFilters, limit = 1000) => api.get<LogSearch>(`/admin/logs?${logQuery(f, { limit: String(limit) })}`)
export const getLevels = () => api.get<LogLevels>('/admin/logs/levels')
export const setLevels = (levels: Record<string, string>, persist: boolean) => api.post<LogLevels>('/admin/logs/levels', { levels, persist })

/** A file from *url* saved under the name the server gives (Content-Disposition), else *fallback*. */
export async function download(url: string, fallback: string): Promise<void> {
  const res = await fetch(url, { headers: { ...authHeaders() } })
  if (!res.ok) throw new Error(`download failed: ${res.status}`)
  const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? fallback
  const a = document.createElement('a')
  a.href = URL.createObjectURL(await res.blob()); a.download = name; a.click()
  URL.revokeObjectURL(a.href)
}

/** 1536 → "1.5 KB", 3_200_000 → "3.1 MB". */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
