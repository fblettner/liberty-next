import { describe, expect, it } from 'vitest'
import { starterView, withStarters } from './aiStarters'
import type { AppAiBody } from '../types/config'

const block: AppAiBody = {
  starters: ['Who can post invoices?'],
  groups: [{ label: 'Accounts', starters: ['Dormant accounts?'] }],
  screens: { users: { starters: ['Explain this user'] } },
  playbooks: { review: { title: 'Review', body: '…' } },
  l: { fr: { groups: [{ label: 'Comptes', starters: ['Comptes dormants ?'] }] } },
}

describe('the starter questions of a language', () => {
  it('shows the lists as written, or those of the language', () => {
    expect(starterView(block, '').starters).toEqual(['Who can post invoices?'])
    expect(starterView(block, 'fr').groups?.[0].label).toBe('Comptes')
    expect(starterView(block, 'fr').starters).toBeUndefined()        // not said in French: the list as written is used
    expect(starterView(block, 'de')).toEqual({})
  })

  it('changes the lists as written without touching the other languages', () => {
    const next = withStarters(block, '', { starters: ['A', 'B'] })
    expect(next.starters).toEqual(['A', 'B'])
    expect(next.l).toEqual(block.l)
    expect(next.playbooks).toEqual(block.playbooks)
  })

  it('changes one language only', () => {
    const next = withStarters(block, 'fr', { starters: ['Qui peut comptabiliser ?'] })
    expect(next.l?.fr).toEqual({ starters: ['Qui peut comptabiliser ?'], groups: block.l!.fr.groups })
    expect(next.starters).toEqual(block.starters)
    expect(block.l?.fr.starters).toBeUndefined()                      // the original is not changed
  })

  it("keeps a screen's questions per language", () => {
    const next = withStarters(block, 'fr', { screens: { users: { starters: ['Expliquer cet utilisateur'] } } })
    expect(next.l?.fr.screens).toEqual({ users: { starters: ['Expliquer cet utilisateur'] } })
    expect(next.screens).toEqual(block.screens)
  })

  it('leaves out what was emptied, down to the language itself', () => {
    const one = withStarters(block, 'fr', { screens: { users: { starters: [] } } })
    expect(one.l?.fr.screens).toBeUndefined()
    const none = withStarters(block, 'fr', { groups: [] })
    expect(none.l).toBeUndefined()
    expect('l' in none).toBe(false)
  })
})
