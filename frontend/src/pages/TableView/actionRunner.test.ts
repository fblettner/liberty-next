// The pure core of the action runner: dotted-path resolution over the chain context, ParamBind
// resolution (value / source / default dispatch) and Condition evaluation with v1's Y/N
// truthiness. Shaped after NOMAJDE's F00926 "Import Security" chain (INPUT + a captured
// select step + a loop element). No network, no React.
import { describe, expect, it } from 'vitest'
import { evalCondition, resolveBinds, resolveSource, type ChainCtx } from './actionRunner'

const ctx: ChainCtx = {
  INPUT: { LYF00950: 'Y', ROLE: 'ADMIN', EMPTY: '' },
  select_workbench: {
    rows: [{ FSSETY: '1', FSOBNM: 'P0101' }, { FSSETY: '3', FSOBNM: 'P4210' }],
    first_row: { FSSETY: '1', FSOBNM: 'P0101' },
    success: true,
  },
  loop: { OBNM: 'P4210' },
}
const form = { USR_ID: 'JDE', usr_name: 'ann' }

describe('resolveSource', () => {
  it('plain keys: chain context first, then the form (case-insensitive)', () => {
    expect(resolveSource('INPUT', ctx, form)).toBe(ctx.INPUT)
    expect(resolveSource('USR_ID', ctx, form)).toBe('JDE')
    expect(resolveSource('USR_NAME', ctx, form)).toBe('ann') // Postgres lower-cases, v1 binds are upper
    expect(resolveSource('missing', ctx, form)).toBeUndefined()
  })
  it('dotted paths walk objects case-insensitively and index arrays numerically', () => {
    expect(resolveSource('INPUT.LYF00950', ctx, form)).toBe('Y')
    expect(resolveSource('input.lyf00950', ctx, form)).toBe('Y')
    expect(resolveSource('select_workbench.first_row.FSOBNM', ctx, form)).toBe('P0101')
    expect(resolveSource('select_workbench.rows.1.FSOBNM', ctx, form)).toBe('P4210')
    expect(resolveSource('select_workbench.rows.9.FSOBNM', ctx, form)).toBeUndefined()
    expect(resolveSource('loop.OBNM', ctx, form)).toBe('P4210')
    expect(resolveSource('nope.deeper', ctx, form)).toBeUndefined()
  })
  it('built-ins resolve from the runtime', () => {
    expect(resolveSource('#SYSDATE#', ctx, form)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(resolveSource('#CURRENT_TIME#', ctx, form)).toMatch(/^\d{2}:\d{2}:\d{2}$/)
    expect(resolveSource('#NOPE#', ctx, form)).toBeUndefined()
  })
})

describe('resolveBinds', () => {
  it('value wins over source; source over default; a literal default binds verbatim', () => {
    const out = resolveBinds(
      [
        { param: 'A', value: 'lit', source: 'INPUT.ROLE' },
        { param: 'B', source: 'INPUT.ROLE', default: 'x' },
        { param: 'C', source: 'INPUT.EMPTY', default: '0' },
        { param: 'D', source: 'INPUT.MISSING' },
      ],
      ctx, form,
    )
    expect(out).toEqual({ A: 'lit', B: 'ADMIN', C: '0' })
    expect('D' in out).toBe(false)
  })
  it('a default that is a built-in or a path is resolved, not bound as text', () => {
    const out = resolveBinds(
      [
        { param: 'TODAY', source: 'INPUT.MISSING', default: '#SYSDATE#' },
        { param: 'OB', source: 'INPUT.MISSING', default: 'select_workbench.first_row.FSOBNM' },
        { param: 'TYPO', source: 'INPUT.MISSING', default: 'no.such.path' },
      ],
      ctx, form,
    )
    expect(out.TODAY).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(out.OB).toBe('P0101')
    expect('TYPO' in out).toBe(false) // a mistyped path never binds the path string itself
  })
})

describe('evalCondition', () => {
  it('equals / not_equals compare as strings; has_rows / no_rows look at arrays', () => {
    expect(evalCondition({ source: 'INPUT.LYF00950', operator: 'equals', value: 'Y' }, ctx, form)).toBe(true)
    expect(evalCondition({ source: 'INPUT.LYF00950', operator: 'not_equals', value: 'Y' }, ctx, form)).toBe(false)
    expect(evalCondition({ source: 'select_workbench.rows', operator: 'has_rows' }, ctx, form)).toBe(true)
    expect(evalCondition({ source: 'INPUT.MISSING', operator: 'no_rows' }, ctx, form)).toBe(true)
  })
  it("truthy follows v1's convention: N / 0 / false / blank are false, anything else true", () => {
    const t = (v: unknown) => evalCondition({ source: 'X', operator: 'truthy' }, { INPUT: {}, X: v }, form)
    for (const falsy of ['N', 'n', 'No', '0', 'false', '', null, undefined, 0, false]) expect(t(falsy)).toBe(false)
    for (const truthy of ['Y', 'y', '1', 'AC', 1, true, 'anything']) expect(t(truthy)).toBe(true)
    expect(evalCondition({ source: 'INPUT.LYF00950', operator: 'falsy' }, ctx, form)).toBe(false)
  })
  it('greater_than / less_than compare numerically', () => {
    expect(evalCondition({ source: 'select_workbench.rows.1.FSSETY', operator: 'greater_than', value: '2' }, ctx, form)).toBe(true)
    expect(evalCondition({ source: 'select_workbench.rows.0.FSSETY', operator: 'less_than', value: '2' }, ctx, form)).toBe(true)
  })
})


describe('navigate', () => {
  it('hands the inline flag to the navigate dependency', async () => {
    const { runChain } = await import('./actionRunner')
    const calls: unknown[][] = []
    const deps = { defaultConnector: 'nomasx1', navigate: (...args: unknown[]) => { calls.push(args) } }
    await runChain([{ id: 'a', type: 'navigate', to: 'roles_get', param_binds: [{ param: 'USR_ID', source: 'usr_id' }], inline: true } as never], {}, { usr_id: '42' }, deps as never)
    await runChain([{ id: 'b', type: 'navigate', screen: 'roles', param_binds: [] } as never], {}, {}, deps as never)
    expect(calls).toEqual([['roles_get', 'nomasx1', { USR_ID: '42' }, undefined, true], ['', 'nomasx1', {}, 'roles', undefined]])
  })
})
