import { describe, expect, it } from 'vitest'
import { applyPresetState, type RunState } from './presetState'

const saved: RunState = {
  params: { apps_id: 1 },
  stepValues: { XREF: { object_id: 'all' } },
  stepEnabled: { SEC: true, LIC: true, LDAP: false, XREF: false },
  logLevel: 'INFO',
}
const security = { name: 'Security', step_enabled: { LIC: false } }
const licences = { name: 'Licences', step_enabled: { SEC: false } }
const everything = { name: 'Everything', step_enabled: { LDAP: true, XREF: true }, params: { apps_id: 2 } }

describe('applyPresetState', () => {
  it('starts from the saved values, not from the previous pick', () => {
    applyPresetState(saved, security)
    expect(applyPresetState(saved, licences).stepEnabled).toEqual({ SEC: false, LIC: true, LDAP: false, XREF: false })
    expect(applyPresetState(saved, everything).stepEnabled).toEqual({ SEC: true, LIC: true, LDAP: true, XREF: true })
  })
  it('layers params, kwargs and log level, and goes back to the saved values', () => {
    const s = applyPresetState(saved, { ...everything, op_kwargs: { XREF: { object_id: 'P01012' } }, log_level: 'DEBUG' })
    expect(s.params).toEqual({ apps_id: 2 })
    expect(s.stepValues.XREF).toEqual({ object_id: 'P01012' })
    expect(s.logLevel).toBe('DEBUG')
    expect(saved.stepValues.XREF).toEqual({ object_id: 'all' })     // the saved copy is untouched
    expect(applyPresetState(saved, null)).toEqual(saved)
  })
})
