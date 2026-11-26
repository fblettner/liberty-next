import { describe, expect, it } from 'vitest'
import { lookupOptions, type LookupData } from './lookups'

const data: LookupData = {
  map: new Map(),
  rows: [
    { ID: '58DE-0001', NAME: 'Daily processing', MENU: 'G01' },
    { ID: '58DE-0002', NAME: 'Periodic processing', MENU: 'G02' },
  ],
  vKey: 'ID', lKey: 'NAME', byColLower: new Map([['id', 'ID'], ['name', 'NAME'], ['menu', 'MENU']]),
}

describe('lookupOptions', () => {
  it('shows the code beside the label', () => {
    expect(lookupOptions(data, undefined, ['MENU'])[0]).toEqual({ value: '58DE-0001', label: 'Daily processing', mono: '58DE-0001', cells: ['G01'] })
  })

  it('leaves the code out when the lookup hides its value, and still saves it', () => {
    const options = lookupOptions(data, undefined, ['MENU'], true)
    expect(options.map((o) => o.mono)).toEqual(['', ''])
    expect(options[1]).toMatchObject({ value: '58DE-0002', label: 'Periodic processing', cells: ['G02'] })
  })
})
