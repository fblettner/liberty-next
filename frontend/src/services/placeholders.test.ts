import { describe, expect, it } from 'vitest'
import { insertAt } from './placeholders'

describe('insertAt', () => {
  it('inserts at the caret, replaces a selection, appends without a caret', () => {
    expect(insertAt('Job  failed', 4, 4, '{JOB}')).toEqual({ text: 'Job {JOB} failed', caret: 9 })
    expect(insertAt('Hello NAME!', 6, 10, '{USER}')).toEqual({ text: 'Hello {USER}!', caret: 12 })
    expect(insertAt('abc', null, null, '{X}')).toEqual({ text: 'abc{X}', caret: 6 })
    expect(insertAt('abc', 99, 120, '{X}')).toEqual({ text: 'abc{X}', caret: 6 })
  })
})
