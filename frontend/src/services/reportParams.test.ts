import { describe, expect, it } from 'vitest'
import { choicesList, choicesProblem, choicesText, cleanChoices } from './reportParams'

describe('a fixed list of choices', () => {
  it('is shown as value = label lines and read back', () => {
    const values = [{ value: 'EN', label: 'English' }, { value: 'FR', label: 'French' }]
    expect(choicesList(choicesText(values))).toEqual(values)
  })
  it('shows a value without a label as itself, and skips empty lines', () => {
    expect(choicesList('EN\n\nFR = French\n')).toEqual([{ value: 'EN', label: 'EN' }, { value: 'FR', label: 'French' }])
  })
})

describe('cleanChoices', () => {
  it('keeps only what the kind uses', () => {
    expect(cleanChoices({ kind: 'connectors', connector: 'demo', query: 'q' })).toEqual({ kind: 'connectors' })
    expect(cleanChoices({ kind: 'schemas', connector_param: ' target ', connector: 'demo', query: 'q' }))
      .toEqual({ kind: 'schemas', connector_param: 'target' })
    expect(cleanChoices({ kind: 'query', connector: 'demo', query: 'companies', value_column: 'CODE', label_column: '' }))
      .toEqual({ kind: 'query', connector: 'demo', query: 'companies', value_column: 'CODE' })
  })
  it('makes the parameter a plain field again when nothing is left', () => {
    expect(cleanChoices(null)).toBeNull()
    expect(cleanChoices({ kind: 'static', values: [] })).toBeNull()
    expect(cleanChoices({ kind: 'static', values: [{ value: ' ', label: 'x' }] })).toBeNull()
    expect(cleanChoices({ kind: 'static', values: [{ value: 'EN', label: 'English' }] }))
      .toEqual({ kind: 'static', values: [{ value: 'EN', label: 'English' }] })
  })
})

describe('choicesProblem', () => {
  it('asks a list read from a database for its connector, its query and its value column', () => {
    expect(choicesProblem({ kind: 'schemas' })).toBe('connector')
    expect(choicesProblem({ kind: 'schemas', connector_param: 'target' })).toBeNull()
    expect(choicesProblem({ kind: 'query', connector: 'demo' })).toBe('query')
    expect(choicesProblem({ kind: 'query', connector: 'demo', query: 'companies' })).toBe('columns')
    expect(choicesProblem({ kind: 'query', connector: 'demo', query: 'companies', value_column: 'CODE' })).toBeNull()
  })
  it('asks nothing of a fixed list or of the connectors', () => {
    expect(choicesProblem({ kind: 'static' })).toBeNull()
    expect(choicesProblem({ kind: 'connectors' })).toBeNull()
    expect(choicesProblem(null)).toBeNull()
  })
})
