import { describe, expect, it } from 'vitest'
import { bindsComplete, resolveBindList } from './dialogHelpers'

describe('nested tab binds', () => {
  const binds = [{ param: 'EAAN8', source: 'WWAN8' }, { param: 'EAIDLN', source: 'WWIDLN' }]
  it('resolve from the parent values, case-insensitively', () => {
    expect(resolveBindList(binds, { wwan8: 1, WWIDLN: 0 })).toEqual({ EAAN8: '1', EAIDLN: '0' })
  })
  it('are complete only when every link has a value', () => {
    // the address number missing (a hidden link column not given): the line number alone would
    // show every address's line-0 email — the tab must load nothing
    const partial = resolveBindList(binds, { WWIDLN: 1 })
    expect(partial).toEqual({ EAIDLN: '1' })
    expect(bindsComplete(binds, partial)).toBe(false)
    expect(bindsComplete(binds, resolveBindList(binds, { WWAN8: 7, WWIDLN: 1 }))).toBe(true)
    // a literal value and a built-in are not the parent's to give
    expect(bindsComplete([{ param: 'X', value: 'A' }], resolveBindList([{ param: 'X', value: 'A' }], {}))).toBe(true)
    expect(bindsComplete([{ param: 'U', source: '#LOGIN_USER#' }], {})).toBe(true)
  })
})
