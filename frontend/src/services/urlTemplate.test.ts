import { describe, expect, it } from 'vitest'
import { fillUrlTemplate, safeUrl } from './urlTemplate'

describe('urlTemplate', () => {
  const values: Record<string, unknown> = { AUUSER: 'JDE USER', '#LOGIN_USER#': 'admin', 'step.first_row.ID': 7, BASE: 'https://jde.example.com/jde' }
  const resolve = (n: string) => values[n]
  it('fills the placeholders, encoded, the built-ins too', () => {
    expect(fillUrlTemplate('https://h/app?user={AUUSER}&by=#LOGIN_USER#&id={step.first_row.ID}', resolve))
      .toBe('https://h/app?user=JDE%20USER&by=admin&id=7')
    expect(fillUrlTemplate('https://h/?x={NOPE}', resolve)).toBe('https://h/?x=')
  })
  it('takes a leading placeholder as the address itself', () => {
    expect(fillUrlTemplate('{BASE}/E1Menu.maf?u={AUUSER}', resolve)).toBe('https://jde.example.com/jde/E1Menu.maf?u=JDE%20USER')
  })
  it('opens only http(s) and paths of this server', () => {
    expect(safeUrl('https://x.y/z')).toBe('https://x.y/z')
    expect(safeUrl('/screen/a/b')).toBe('/screen/a/b')
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', '//evil.example', 'ftp://x', '']) expect(safeUrl(bad)).toBeNull()
  })
})
