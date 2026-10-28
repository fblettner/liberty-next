// The Developer page's data (C12): fetch + the filters, no React.
import { api } from '../api/client'
import type { ConfigKey, ConfigReference } from '../types/devref'

export const getConfigReference = () => api.get<ConfigReference>('/admin/dev/config')

/** A key's status: edited in a screen, file-only on purpose, or a gap. */
export function keyStatus(k: ConfigKey): 'editor' | 'file' | 'gap' {
  return k.editor ? 'editor' : k.file_only ? 'file' : 'gap'
}

/** The keys matching *q* (key, description, a reader's file) in *section*, gaps only when asked. */
export function filterKeys(keys: ConfigKey[], q: string, section: string, onlyGaps: boolean): ConfigKey[] {
  const n = q.trim().toLowerCase()
  return keys.filter((k) =>
    (!section || k.section === section)
    && (!onlyGaps || keyStatus(k) === 'gap')
    && (!n || k.key.toLowerCase().includes(n) || (k.description ?? '').toLowerCase().includes(n)
      || k.readers.some((r) => r.file.toLowerCase().includes(n))))
}

/** The modules that read *k*, from its readers' files: ``liberty/jobs/files.py`` → ``jobs``,
 *  ``liberty/main.py`` → ``main`` — in first-seen order, once each. */
export function readerModules(k: ConfigKey): string[] {
  const out: string[] = []
  for (const r of k.readers) {
    const parts = r.file.split('/')
    const mod = parts.length > 2 ? parts[1] : (parts[parts.length - 1] ?? '').replace(/\.py$/, '')
    if (mod && !out.includes(mod)) out.push(mod)
  }
  return out
}

/** Per section: how many keys, how many still not in the UI — the left list. */
export function sectionCounts(keys: ConfigKey[]): { section: string; total: number; gaps: number }[] {
  const by = new Map<string, { section: string; total: number; gaps: number }>()
  for (const k of keys) {
    const s = by.get(k.section) ?? { section: k.section, total: 0, gaps: 0 }
    s.total += 1
    if (keyStatus(k) === 'gap') s.gaps += 1
    by.set(k.section, s)
  }
  return [...by.values()]
}
