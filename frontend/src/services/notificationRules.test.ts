import { describe, expect, it } from 'vitest'
import { actionRules, placeholdersIn, rulePlaceholders } from './notificationRules'

describe('placeholdersIn', () => {
  it('reads the names once, in order', () => {
    expect(placeholdersIn('Period {PERIOD} of {company} closed — {PERIOD}')).toEqual(['PERIOD', 'company'])
  })
  it('keeps literal braces and reads the name of a formatted value', () => {
    expect(placeholdersIn('{{not}} {amount:>10} {row.name} {list[0]}')).toEqual(['amount', 'row', 'list'])
  })
  it('answers nothing for no text', () => {
    expect(placeholdersIn(null)).toEqual([])
    expect(placeholdersIn('')).toEqual([])
  })
})

describe('rulePlaceholders', () => {
  it('gathers subject, body, link and the call, without what the server gives', () => {
    expect(rulePlaceholders({
      subject: 'Order {ORDER} approved by {USER}',
      body: 'Amount {amount}',
      link: '/screen/app/orders?order={order}',
      action: { params: { NOTE: 'for {customer}', COUNT: 3 } },
    })).toEqual(['ORDER', 'amount', 'customer'])
  })
})

describe('actionRules', () => {
  it('keeps only the rules a step may send', () => {
    const rules = actionRules({
      on_job: { trigger: { kind: 'job' }, subject: 'Job failed' },
      b_rule: { trigger: { kind: 'action' }, subject: 'Hello {NAME}', enabled: false },
      a_rule: { trigger: { kind: 'action' }, label: 'Approval', subject: 'x' },
    })
    expect(rules.map((r) => r.id)).toEqual(['a_rule', 'b_rule'])
    expect(rules[0]).toMatchObject({ label: 'Approval', enabled: true, placeholders: [] })
    expect(rules[1]).toMatchObject({ label: 'Hello {NAME}', enabled: false, placeholders: ['NAME'] })
  })
  it('answers an empty list for no file', () => {
    expect(actionRules(undefined)).toEqual([])
  })
})
