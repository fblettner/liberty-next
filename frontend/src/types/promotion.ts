// The promotion API (/admin/promotion): target instances, a push's answer, the history.
import type { PackagePlan } from './packagePlan'
import type { BundleReport } from './changesets'

export interface PromotionTarget {
  label?: string
  url: string
  username?: string
  /** As stored: ``ENC:…`` (encrypted here) or ``${VAR}``; a typed value is encrypted on save. */
  password?: string
  verify_ssl?: boolean
  timeout_seconds?: number
}

export type PromotionPayload =
  | { kind: 'config'; seeds: { kind: string; name: string; scope?: string | null }[]; include?: { kind: string; name: string; scope?: string | null }[] }
  | { kind: 'changes'; packageId: string; name: string }

export interface PushResult {
  target: { name: string; label: string; url: string; instance?: string | null; version?: string | null }
  payload: 'config' | 'changes'
  label: string
  applied: boolean
  /** A config package's plan, or a change bundle's drift report — the target's answer. */
  report: PackagePlan | BundleReport
  history_id?: string
}

export interface HistoryEntry {
  id: string
  at: string
  user: string
  target: string
  target_url: string
  target_version?: string | null
  payload: 'config' | 'changes'
  label: string
  strategy?: string | null
  comment: string
  status: 'applied' | 'partial'
  summary: Record<string, number>
  report?: PackagePlan | BundleReport
}
