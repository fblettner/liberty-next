import { describe, expect, it } from 'vitest'
import { can, nomaflowAccess } from './permissions'

const user = (permissions: string[], is_superuser = false) => ({ permissions, is_superuser })

describe('can / nomaflowAccess', () => {
  it('matches like the backend, a superuser holding everything', () => {
    expect(can(user(['job:*']), 'job:ping')).toBe(true)
    expect(can(user(['job:*', '!job:ping']), 'job:ping')).toBe(false)
    expect(can(user([], true), 'anything')).toBe(true)
    expect(can(null, 'job:ping')).toBe(false)
    // what a menu grant gives, as the server says it at sign-in — a deny by name still wins
    expect(can({ is_superuser: false, permissions: ['menu:shop:sales'], implied: ['sql:shop:orders_get'] }, 'sql:shop:orders_get')).toBe(true)
    expect(can({ is_superuser: false, permissions: ['menu:shop:sales'], implied: ['sql:shop:orders_get'] }, 'sql:shop:orders_put')).toBe(false)
    expect(can({ is_superuser: false, permissions: ['!sql:shop:orders_get'], implied: ['sql:shop:orders_get'] }, 'sql:shop:orders_get')).toBe(false)
  })
  it('reads the nomaflow grants', () => {
    const op = nomaflowAccess(user(['nomaflow:view', 'job:ping']))
    expect([op.view, op.edit, op.run('ping'), op.run('other'), op.policies]).toEqual([true, false, true, false, false])
    const ed = nomaflowAccess(user(['nomaflow:edit']))
    expect([ed.view, ed.edit, ed.run('ping')]).toEqual([true, true, false])
    expect(nomaflowAccess(user([], true)).policies).toBe(true)
  })
})
