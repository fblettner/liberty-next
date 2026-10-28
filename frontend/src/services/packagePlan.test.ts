import { describe, expect, it } from 'vitest'
import { pendingChanges, planCounts, shownEntities, splitKind } from './packagePlan'
import type { PackagePlan, PlanEntity } from '../types/packagePlan'

const e = (kind: string, name: string, state: PlanEntity['state']): PlanEntity => ({ kind, name, state })

describe('splitKind', () => {
  it('splits the scope off', () => {
    expect(splitKind('query/foo')).toEqual({ kind: 'query', scope: 'foo' })
    expect(splitKind('job')).toEqual({ kind: 'job', scope: null })
    expect(splitKind('menu_item/app/x')).toEqual({ kind: 'menu_item', scope: 'app/x' })
  })
})

describe('shownEntities', () => {
  const list = [e('screen/a', 'z', 'identical'), e('screen/a', 'b', 'new'), e('job', 'j', 'changed'), e('screen/a', 'a', 'new')]
  it('orders by state then kind then name, identical hidden by default', () => {
    expect(shownEntities(list, false).map((x) => x.name)).toEqual(['j', 'a', 'b'])
    expect(shownEntities(list, true).map((x) => x.name)).toEqual(['j', 'a', 'b', 'z'])
  })
  it('does not reorder the input', () => {
    shownEntities(list, true)
    expect(list[0].name).toBe('z')
  })
})

describe('planCounts / pendingChanges', () => {
  const plan: PackagePlan = {
    files: [{ file: 'screens.toml', strategy: 'overwrite', added: [], replaced: [], skipped: [], errors: ['x'],
      entities: [e('s', 'a', 'new'), e('s', 'b', 'changed'), e('s', 'c', 'identical'), e('s', 'd', 'removed')] }],
    warnings: [], reloaded: false,
  }
  it('counts from the entities when the server sent no summary', () => {
    expect(planCounts(plan)).toMatchObject({ new: 1, changed: 1, identical: 1, removed: 1, errors: 1 })
    expect(pendingChanges(plan)).toBe(3)
  })
  it('prefers the server summary', () => {
    expect(pendingChanges({ ...plan, summary: { new: 5, changed: 0, removed: 0 } })).toBe(5)
  })
})
