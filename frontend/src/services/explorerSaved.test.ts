import { describe, expect, it } from 'vitest'
import { groupFound, matchSaved, savedCounts, type FoundSaved } from './explorerSaved'

const item = (over: Partial<FoundSaved>): FoundSaved => ({ app: 'nomajde', model: 'security', model_label: 'Security', kind: 'analyses', id: 'x', name: 'X', shared: true, ...over })
const FOUND: FoundSaved[] = [
  item({ id: 'users-by-role', name: 'Users by role', audience: 'audit' }),
  item({ id: 'home', name: 'Home', kind: 'dashboards' }),
  item({ app: 'nomasx1', model: 'security', id: 'dormant', name: 'Comptes dormants', description: 'Sans connexion depuis 90 jours', shared: false, owner: 'ann' }),
  item({ app: 'nomasx1', model: 'licenses', model_label: 'Licence compliance', id: 'gap', name: 'Subscribed vs used' }),
]

describe('finding a saved analysis or dashboard whatever its model', () => {
  it('counts what each model holds', () => {
    expect(savedCounts(FOUND)).toEqual({
      'nomajde/security': { analyses: 1, dashboards: 1 }, 'nomasx1/security': { analyses: 1, dashboards: 0 }, 'nomasx1/licenses': { analyses: 1, dashboards: 0 } })
    expect(savedCounts([])).toEqual({})
  })

  it('matches every word typed, in the name, the description, the audience, the owner or the model', () => {
    expect(matchSaved(FOUND, 'role').map((f) => f.id)).toEqual(['users-by-role'])
    expect(matchSaved(FOUND, 'DORMANTS connexion').map((f) => f.id)).toEqual(['dormant'])        // two words, case ignored
    expect(matchSaved(FOUND, 'licence').map((f) => f.id)).toEqual(['gap'])                          // by its model's name
    expect(matchSaved(FOUND, 'ann').map((f) => f.id)).toEqual(['dormant'])                          // by its owner
    expect(matchSaved(FOUND, 'security home').map((f) => f.id)).toEqual(['home'])
    expect(matchSaved(FOUND, 'nothing like it')).toEqual([])
    expect(matchSaved(FOUND, '  ')).toHaveLength(4)                                                 // nothing typed: everything
  })

  it('ignores accents both ways', () => {
    const f = [item({ id: 'p', name: 'Quantité par pôle' })]
    expect(matchSaved(f, 'quantite pole')).toHaveLength(1)
    expect(matchSaved([item({ id: 'q', name: 'Quantite' })], 'quantité')).toHaveLength(1)
  })

  it('keeps one kind when asked', () => {
    expect(matchSaved(FOUND, '', 'dashboards').map((f) => f.id)).toEqual(['home'])
    expect(matchSaved(FOUND, 'security', 'analyses').map((f) => f.id)).toEqual(['users-by-role', 'dormant'])
  })

  it('groups what was found under its model, in the order received', () => {
    const groups = groupFound(FOUND)
    expect(groups.map((g) => [g.key, g.label, g.items.length])).toEqual([['nomajde/security', 'Security', 2], ['nomasx1/security', 'Security', 1], ['nomasx1/licenses', 'Licence compliance', 1]])
  })
})
