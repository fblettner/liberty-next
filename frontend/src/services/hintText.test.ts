import { describe, expect, it } from 'vitest'
import { hintParts } from './hintText'

describe('hintParts', () => {
  it('leaves plain text whole', () => {
    expect(hintParts('The name shown in the menu.')).toEqual([{ text: 'The name shown in the menu.', code: false }])
  })
  it('takes what is between backticks as code, without them', () => {
    expect(hintParts('Blank uses the column ``name`` as the key; set to `""` to opt out.')).toEqual([
      { text: 'Blank uses the column ', code: false },
      { text: 'name', code: true },
      { text: ' as the key; set to ', code: false },
      { text: '""', code: true },
      { text: ' to opt out.', code: false },
    ])
  })
  it('drops the emphasis marks of the plain text only', () => {
    expect(hintParts('Pick a **Dialect**, an *async* one; ``a * b`` stays.')).toEqual([
      { text: 'Pick a Dialect, an async one; ', code: false },
      { text: 'a * b', code: true },
      { text: ' stays.', code: false },
    ])
  })
  it('gives nothing for an empty text', () => {
    expect(hintParts('')).toEqual([])
  })
})
