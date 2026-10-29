// The Retention page's calls and plain helpers (no React).
import { api } from '../api/client'
import { formatBytes } from './serverLog'

export interface StoreStatus {
  id: 'runs' | 'work_area' | 'notifications' | 'config_versions' | 'change_packages' | 'saved_views' | 'doc_reports' | 'server_log'
  policy: Record<string, unknown>
  where: string
  size: Record<string, unknown>
  can_run: boolean
}
export interface RunResult { store: string; dry_run: boolean; deleted: number; bytes?: number; detail?: Record<string, number> }

export const getRetention = () => api.get<{ stores: StoreStatus[] }>('/admin/retention')
export const runRetention = (store: string, dryRun: boolean) => api.post<RunResult>(`/admin/retention/${encodeURIComponent(store)}/run?dry_run=${dryRun}`)

type T = (key: string, opts?: Record<string, unknown>) => string
const n = (v: unknown) => Number(v ?? 0)

/** What the store keeps, in words (the policy). */
export function policyText(s: StoreStatus, t: T): string {
  const p = s.policy
  switch (s.id) {
    case 'runs': return t('retentionCenter.policy.runs', { days: n(p.days), cap: n(p.keep_last_per_job) }) + (p.enabled === false ? ` · ${t('retentionCenter.off')}` : '')
    case 'work_area': return n(p.days) > 0 ? t('retentionCenter.policy.days', { days: n(p.days) }) : t('retentionCenter.keepAll')
    case 'notifications': return n(p.days) > 0 ? t('retentionCenter.policy.readDays', { days: n(p.days) }) : t('retentionCenter.keepAll')
    case 'config_versions': return t('retentionCenter.policy.versions', { max: n(p.max_versions), days: n(p.max_age_days) })
    case 'change_packages': return n(p.days) > 0 ? t('retentionCenter.policy.packages', { days: n(p.days) }) : t('retentionCenter.keepAll')
    case 'saved_views': return t('retentionCenter.policy.views')
    case 'doc_reports': return t('retentionCenter.policy.docReports')
    case 'server_log': return p.enabled === false ? t('retentionCenter.off') : t('retentionCenter.policy.log', { mb: n(p.max_mb), backups: n(p.backups) })
  }
}

/** How big the store is, in words. */
export function sizeText(s: StoreStatus, t: T): string {
  const z = s.size
  const parts: string[] = []
  if (z.rows !== undefined) parts.push(t('retentionCenter.size.rows', { n: n(z.rows) }))
  if (z.total !== undefined) parts.push(t('retentionCenter.size.notifications', { n: n(z.total), read: n(z.read) }))
  if (z.versions !== undefined) parts.push(t('retentionCenter.size.versions', { n: n(z.versions) }))
  if (z.packages !== undefined) parts.push(t('retentionCenter.size.packages', { n: n(z.packages), logs: n(z.import_logs) }))
  if (z.views !== undefined) parts.push(t('retentionCenter.size.views', { n: n(z.views) }))
  if (z.files !== undefined && z.versions === undefined) parts.push(t('retentionCenter.size.files', { count: n(z.files) }))
  if (z.bytes !== undefined) parts.push(formatBytes(n(z.bytes)))
  return parts.join(' · ')
}
