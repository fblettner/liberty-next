import { describe, expect, it } from 'vitest'
import { connectorLink, jobValues } from './deepLinks'

describe('links that open a page on something', () => {
  it("reads a job's values from its own address only", () => {
    expect(jobValues('/job/xref-object', '?object_id=P01012&apps_id=1', 'xref-object')).toEqual({ object_id: 'P01012', apps_id: '1' })
    expect(jobValues('/job/another', '?object_id=P01012', 'xref-object')).toEqual({})
    expect(jobValues('/job/xref-object', '', 'xref-object')).toEqual({})
    expect(jobValues('/job/with%20space', '?a=b', 'with space')).toEqual({ a: 'b' })
  })
  it('reads what the Connectors page is asked to open', () => {
    expect(connectorLink('/liberty/connectors', '?connector=ais_connection&create=application&q=P01012')).toEqual({ connector: 'ais_connection', create: 'application', q: 'P01012' })
    expect(connectorLink('/liberty/connectors', '?connector=ais_connection')).toEqual({ connector: 'ais_connection' })
    expect(connectorLink('/liberty/connectors', '?connector=x&q=lost')).toEqual({ connector: 'x' })      // a text without a generator means nothing
    expect(connectorLink('/liberty/connectors', '')).toBeNull()
    expect(connectorLink('/liberty/screens', '?connector=x')).toBeNull()
  })
})
