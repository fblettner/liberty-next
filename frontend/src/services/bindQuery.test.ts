import { describe, expect, it } from 'vitest'
import { bindsToQuery } from './bindQuery'

describe('bindsToQuery', () => {
  it('asks for an exact match on every bind', () => {
    // without the companion a text filter column is read with "contains": DEMO would also give DEMO2
    expect(bindsToQuery({ USR_ID: 'DEMO' })).toBe('?USR_ID=DEMO&USR_ID_op=equals')
    expect(bindsToQuery({ A: '1', B: '2' })).toBe('?A=1&A_op=equals&B=2&B_op=equals')
  })
  it('sends nothing when there is no bind and no screen', () => {
    expect(bindsToQuery({})).toBe('')
    expect(bindsToQuery({}, null)).toBe('')
  })
  it('names the screen whose settings the read uses', () => {
    expect(bindsToQuery({ ID: '7' }, { app: 'demo', screen: 'orders_form' }))
      .toBe('?ID=7&ID_op=equals&_screen=orders_form&_app=demo')
    expect(bindsToQuery({}, { app: 'demo', screen: 'orders_form' })).toBe('?_screen=orders_form&_app=demo')
  })
  it('leaves an incomplete pin out', () => {
    expect(bindsToQuery({ ID: '7' }, { app: '', screen: 'orders_form' })).toBe('?ID=7&ID_op=equals')
  })
  it('encodes names and values', () => {
    expect(bindsToQuery({ 'A B': 'x&y=z' })).toBe('?A%20B=x%26y%3Dz&A%20B_op=equals')
  })
})
