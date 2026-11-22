import { describe, expect, it } from 'vitest'
import { failureOf, messageKey, targetOf, type SaveProblem } from './saveProblems'

const problem = (path: (string | number)[], code = 'missing', message = 'is required'): SaveProblem =>
  ({ path, where: '', message, code })

describe('failureOf', () => {
  it('reads the problems a refused save lists', () => {
    const e = Object.assign(new Error('Screen "Orders" › Rules: must be text'), {
      problems: [problem(['screens', 'demo', 'orders', 'columns', 0, 'rules'], 'string_type', 'must be text')],
    })
    const f = failureOf(e)
    expect(f.message).toBe('Screen "Orders" › Rules: must be text')
    expect(f.problems).toHaveLength(1)
  })
  it('gives the message alone for any other error', () => {
    expect(failureOf(new Error('HTTP 500'))).toEqual({ message: 'HTTP 500', problems: [] })
    expect(failureOf('boom')).toEqual({ message: 'boom', problems: [] })
  })
  it('leaves out what is not a problem', () => {
    const e = Object.assign(new Error('x'), { problems: [null, { path: 'a' }, problem(['jobs', 0])] })
    expect(failureOf(e).problems).toHaveLength(1)
  })
})

describe('targetOf', () => {
  it('finds a screen, a chart, a dashboard, a model by application and id', () => {
    expect(targetOf('screens', ['screens', 'demo', 'orders', 'columns', 3, 'dd']))
      .toEqual({ scope: 'demo', id: 'orders', inner: ['columns', 3, 'dd'] })
    expect(targetOf('dashboards', ['dashboards', 'demo', 'home'])).toEqual({ scope: 'demo', id: 'home', inner: [] })
    expect(targetOf('explorer', ['explorer', 'demo', 'sales', 'entities', 'orders', 'key']))
      .toEqual({ scope: 'demo', id: 'sales', inner: ['entities', 'orders', 'key'] })
  })
  it('finds a menu entry by its place', () => {
    expect(targetOf('menus', ['menus', 'demo', 'items', 4, 'target']))
      .toEqual({ scope: 'demo', index: 4, inner: ['target'] })
    expect(targetOf('menus', ['menus', 'demo', 'label'])).toEqual({ scope: 'demo', index: undefined, inner: ['label'] })
  })
  it('finds a query, a table or an endpoint of a connector', () => {
    expect(targetOf('connectors', ['connectors', 'demo', 'queries', 3, 'sql']))
      .toEqual({ scope: 'demo', kind: 'queries', id: undefined, index: 3, inner: ['sql'] })
    expect(targetOf('connectors', ['connectors', 'demo', 'endpoints', 'orders', 'path']))
      .toEqual({ scope: 'demo', kind: 'endpoints', id: 'orders', index: undefined, inner: ['path'] })
    expect(targetOf('connectors', ['connectors', 'demo', 'pool'])).toEqual({ scope: 'demo', inner: ['pool'] })
  })
  it('finds a dictionary record, shared or of an application', () => {
    expect(targetOf('dictionary', ['entries', 'STATUS', 'decimals']))
      .toEqual({ scope: '', kind: 'entries', id: 'STATUS', inner: ['decimals'] })
    expect(targetOf('dictionary', ['connectors', 'demo', 'lookups', 'customers', 'query']))
      .toEqual({ scope: 'demo', kind: 'lookups', id: 'customers', inner: ['query'] })
  })
  it('finds a job and what is inside it', () => {
    expect(targetOf('jobs', ['jobs', 2, 'steps', 1, 'type'])).toEqual({ index: 2, inner: ['steps', 1, 'type'] })
  })
  it('finds a service, a rule, a pool, a section of the settings', () => {
    expect(targetOf('actions', ['actions', 'close_period', 'steps', 0])).toEqual({ id: 'close_period', inner: ['steps', 0] })
    expect(targetOf('notifications', ['notifications', 'late'])).toEqual({ id: 'late', inner: [] })
    expect(targetOf('pools', ['pools', 'main', 'url'])).toEqual({ id: 'main', inner: ['url'] })
    expect(targetOf('app', ['notify', 'smtp_port'])).toEqual({ id: 'notify', inner: ['smtp_port'] })
  })
  it('says nothing when the path does not reach an object', () => {
    expect(targetOf('screens', ['screens', 'demo'])).toBeNull()
    expect(targetOf('screens', ['charts', 'demo', 'x'])).toBeNull()
    expect(targetOf('jobs', ['meta', 'retention'])).toBeNull()
    expect(targetOf('theme', ['theme', 'vars'])).toBeNull()
  })
})

describe('messageKey', () => {
  it('names the translation of the refusals said the same way everywhere', () => {
    expect(messageKey(problem(['a', 'label']))).toEqual({ key: 'saveProblems.code.missing', name: 'label' })
    expect(messageKey(problem(['a', 'n'], 'int_parsing'))).toEqual({ key: 'saveProblems.code.integer', name: 'n' })
    expect(messageKey(problem(['a', 'tabel'], 'extra_forbidden'))).toEqual({ key: 'saveProblems.code.unknown', name: 'tabel' })
  })
  it('keeps the server sentence when it carries values', () => {
    expect(messageKey(problem(['a'], 'literal_error', 'must be one of: a, b'))).toBeNull()
    expect(messageKey(problem(['a'], 'value_error', 'no such query'))).toBeNull()
  })
})
