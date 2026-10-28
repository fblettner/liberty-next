// The /admin/promotion routes (C11): targets, push (preview / apply on a target), history.
import { api } from '../api/client'
import type { HistoryEntry, PromotionPayload, PromotionTarget, PushResult } from '../types/promotion'

export const getTargets = () => api.get<{ targets: Record<string, PromotionTarget> }>('/admin/promotion/targets').then((r) => r.targets ?? {})
export const saveTargets = (targets: Record<string, PromotionTarget>) => api.put<{ targets: Record<string, PromotionTarget> }>('/admin/promotion/targets', { targets })
export const testTarget = (name: string) => api.post<{ ok: boolean; name?: string | null; version?: string | null }>(`/admin/promotion/targets/${encodeURIComponent(name)}/test`)
export const getHistory = () => api.get<{ entries: HistoryEntry[] }>('/admin/promotion/history').then((r) => r.entries)
export const getHistoryEntry = (id: string) => api.get<HistoryEntry>(`/admin/promotion/history/${encodeURIComponent(id)}`)

/** Preview (``apply`` false) or apply *payload* on *target*. */
export function push(target: string, payload: PromotionPayload, opts: { apply: boolean; strategy?: string; force?: number[]; comment?: string }): Promise<PushResult> {
  const body = payload.kind === 'config'
    ? { target, payload: 'config', seeds: payload.seeds, include: payload.include, strategy: opts.strategy ?? 'overwrite' }
    : { target, payload: 'changes', package_id: payload.packageId, force: opts.force ?? [] }
  return api.post<PushResult>('/admin/promotion/push', { ...body, apply: opts.apply, comment: opts.comment ?? '' })
}

/** A push's report is a config package plan (it has ``files``) or a change bundle report. */
export function isPlan(report: PushResult['report']): report is Extract<PushResult['report'], { files: unknown }> {
  return Array.isArray((report as { files?: unknown }).files)
}
