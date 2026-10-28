import { describe, expect, it } from 'vitest'
import { policyText, sizeText, type StoreStatus } from './retentionCenter'

const t = (key: string, opts?: Record<string, unknown>) => `${key}${opts ? JSON.stringify(opts) : ''}`
const s = (id: StoreStatus['id'], policy: Record<string, unknown>, size: Record<string, unknown> = {}): StoreStatus => ({ id, policy, size, where: '', can_run: true })

describe('retentionCenter', () => {
  it('words the policies', () => {
    expect(policyText(s('runs', { days: 30, keep_last_per_job: 100, enabled: true }), t)).toBe('retentionCenter.policy.runs{"days":30,"cap":100}')
    expect(policyText(s('work_area', { days: 0 }), t)).toBe('retentionCenter.keepAll')
    expect(policyText(s('change_packages', { days: 365 }), t)).toBe('retentionCenter.policy.packages{"days":365}')
    expect(policyText(s('server_log', { enabled: false }), t)).toBe('retentionCenter.off')
  })
  it('words the sizes', () => {
    expect(sizeText(s('work_area', {}, { files: 3, bytes: 2048 }), t)).toBe('retentionCenter.size.files{"count":3} · 2.0 KB')
    expect(sizeText(s('config_versions', {}, { versions: 12, files: 13, bytes: 900 }), t)).toBe('retentionCenter.size.versions{"n":12} · 900 B')
    expect(sizeText(s('notifications', {}, { total: 9, read: 4 }), t)).toBe('retentionCenter.size.notifications{"n":9,"read":4}')
  })
})
