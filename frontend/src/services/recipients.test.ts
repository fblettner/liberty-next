import { describe, expect, it } from 'vitest'
import { addRecipient, parseRecipient, recipientOf, repaired, unreadable } from './recipients'

describe('recipients', () => {
  it('takes a line apart', () => {
    expect(parseRecipient('role:auditor')).toEqual({ kind: 'role', name: 'auditor' })
    expect(parseRecipient('User: jdoe ')).toEqual({ kind: 'user', name: 'jdoe' })
    expect(parseRecipient('email:ops@example.com')).toEqual({ kind: 'email', name: 'ops@example.com' })
    expect(parseRecipient('admin')).toEqual({ kind: null, name: 'admin' })
    expect(parseRecipient('team:ops')).toEqual({ kind: null, name: 'team:ops' })
  })
  it('writes a line, and none without a name', () => {
    expect(recipientOf('role', ' auditor ')).toBe('role:auditor')
    expect(recipientOf('user', '  ')).toBe('')
  })
  it('adds a recipient once', () => {
    expect(addRecipient(['role:auditor'], 'user', 'jdoe')).toEqual(['role:auditor', 'user:jdoe'])
    expect(addRecipient(['role:auditor'], 'role', 'auditor')).toEqual(['role:auditor'])
    expect(addRecipient([], 'email', '')).toEqual([])
  })
  it('names the lines that would go nowhere', () => {
    expect(unreadable(['role:auditor', 'admin', 'user:', 'email:ops@example.com'])).toEqual(['admin', 'user:'])
  })
  it('gives an address typed alone its kind, and leaves a bare word', () => {
    expect(repaired('ops@example.com')).toBe('email:ops@example.com')
    expect(repaired('admin')).toBe('admin')
    expect(repaired('role:auditor')).toBe('role:auditor')
  })
})
