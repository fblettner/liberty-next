import { describe, expect, it } from 'vitest'
import { checkJob, missingFields } from './jobChecks'

const copy = (name: string, extra: Record<string, unknown> = {}) =>
  ({ type: 'sql_copy', name, source: { connector: 'a', query: 'q' }, target: { connector: 'b', table: 't' }, ...extra })

describe('missingFields', () => {
  it('says what each kind of step cannot run without', () => {
    expect(missingFields({ type: 'sql_copy', name: 's' })).toEqual(['source', 'target'])
    expect(missingFields({ type: 'sql_query', name: 's', connector: 'demo' })).toEqual(['query'])
    expect(missingFields({ type: 'python', name: 's', callable: 'app.mod:fn' })).toEqual([])
    expect(missingFields({ type: 'notify', name: 's', recipients: [], subject: 'Done' })).toEqual(['recipients'])
    expect(missingFields({ type: 'file_load', name: 's', target: {} })).toEqual(['target'])
  })
  it('reads an LDAP directory from a pool or from the step', () => {
    expect(missingFields({ type: 'ldap_sync', name: 's', pool: 'directory', target_connector: 'c', target_query: 'q' })).toEqual([])
    expect(missingFields({ type: 'ldap_sync', name: 's', target_connector: 'c', target_query: 'q' }))
      .toEqual(['server', 'bind_dn', 'search_base'])
  })
  it('asks nothing of a kind it does not know', () => {
    expect(missingFields({ type: 'future_kind', name: 's' })).toEqual([])
  })
})

describe('checkJob', () => {
  it('finds nothing in a complete job', () => {
    expect(checkJob({ id: 'nightly-load', steps: [copy('load')] }, ['other'])).toEqual([])
  })
  it('asks for an id, a usable one, a free one', () => {
    expect(checkJob({ id: '  ', steps: [] }, [])).toEqual([{ code: 'idRequired' }])
    expect(checkJob({ id: 'nightly load', steps: [] }, [])).toEqual([{ code: 'idCharacters' }])
    expect(checkJob({ id: 'nightly', steps: [] }, ['nightly'])).toEqual([{ code: 'idTaken', name: 'nightly' }])
  })
  it('places a problem on its step', () => {
    const issues = checkJob({ id: 'j', steps: [copy('load'), { type: 'sql_query', name: '' }, copy('load')] }, [])
    expect(issues).toEqual([
      { code: 'stepName', step: 1 },
      { code: 'stepMissing', step: 1, fields: ['connector', 'query'] },
      { code: 'stepDuplicate', step: 2, name: 'load' },
    ])
  })
  it('asks for both fixed dates of a report, or neither', () => {
    const report = (extra: Record<string, unknown>) => ({ type: 'report', name: 'monthly', report: 'analysis:sales', ...extra })
    expect(checkJob({ id: 'j', steps: [report({})] }, [])).toEqual([])
    expect(checkJob({ id: 'j', steps: [report({ window_from: '2026-01-01', window_to: '2026-01-31' })] }, [])).toEqual([])
    expect(checkJob({ id: 'j', steps: [report({ window_from: '2026-01-01' })] }, [])).toEqual([{ code: 'stepWindow', step: 0 }])
  })
  it('asks a preset for a name, and a scheduled one for its own', () => {
    expect(checkJob({ id: 'j', steps: [], presets: [{ name: 'a' }, { name: 'a' }, { name: ' ' }] }, []))
      .toEqual([{ code: 'presetName', preset: 2 }])
    expect(checkJob({ id: 'j', steps: [], presets: [{ name: 'a', schedule: '0 2 * * *' }, { name: 'a', schedule: '0 3 * * *' }] }, []))
      .toEqual([{ code: 'presetDuplicate', preset: 1, name: 'a' }])
  })
  it('knows the jobs a step may call', () => {
    const call = (target: string) => ({ type: 'call_job', name: 'next', target_job_id: target })
    expect(checkJob({ id: 'j', steps: [call('cleanup')] }, ['cleanup'])).toEqual([])
    expect(checkJob({ id: 'j', steps: [call('clean-up')] }, ['cleanup']))
      .toEqual([{ code: 'stepUnknownJob', step: 0, name: 'clean-up' }])
  })
})
