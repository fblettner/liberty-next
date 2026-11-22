import { describe, expect, it } from 'vitest'
import { localizedCount, localizedValue, otherLanguages, prunedLocalized, withLocalized } from './localized'

describe('a text in several languages', () => {
  it('offers every language but the one the configuration is written in', () => {
    expect(otherLanguages(['en', 'fr', 'de'], 'en')).toEqual(['fr', 'de'])
    expect(otherLanguages(['en', 'fr'], 'fr-FR')).toEqual(['en'])
    expect(otherLanguages(['en', 'fr', 'fr'], 'de')).toEqual(['en', 'fr'])
  })

  it('reads a field in a language, blank when it is not given', () => {
    const l = { fr: { label: 'Ventes', max: 3 } }
    expect(localizedValue(l, 'fr', 'label')).toBe('Ventes')
    expect(localizedValue(l, 'fr', 'description')).toBe('')
    expect(localizedValue(l, 'fr', 'max')).toBe('')          // not a text
    expect(localizedValue(undefined, 'fr', 'label')).toBe('')
  })

  it('sets a field without touching what it was not asked about', () => {
    const l = { fr: { label: 'Ventes', title_column: 'TITLE_FR' } }
    const next = withLocalized(l, 'fr', 'description', 'Par mois')
    expect(next).toEqual({ fr: { label: 'Ventes', title_column: 'TITLE_FR', description: 'Par mois' } })
    expect(l).toEqual({ fr: { label: 'Ventes', title_column: 'TITLE_FR' } })   // the original is not changed
    expect(withLocalized(undefined, 'de', 'label', 'Umsatz')).toEqual({ de: { label: 'Umsatz' } })
  })

  it('takes a blanked field out, and a language left with nothing', () => {
    expect(withLocalized({ fr: { label: 'Ventes', description: 'x' } }, 'fr', 'description', '  ')).toEqual({ fr: { label: 'Ventes' } })
    expect(withLocalized({ fr: { label: 'Ventes' }, de: { label: 'Umsatz' } }, 'fr', 'label', '')).toEqual({ de: { label: 'Umsatz' } })
  })

  it('is left out of what is saved when it holds nothing', () => {
    expect(prunedLocalized({})).toBeUndefined()
    expect(prunedLocalized({ fr: {} })).toBeUndefined()
    expect(prunedLocalized({ fr: { label: ' ' } })).toBeUndefined()
    expect(prunedLocalized({ fr: { label: 'Ventes', description: '' } })).toEqual({ fr: { label: 'Ventes' } })
  })

  it('counts the texts given', () => {
    const l = { fr: { label: 'Ventes', description: 'x', title_column: 'T' }, de: { label: 'Umsatz' } }
    expect(localizedCount(l, ['label', 'description'])).toBe(3)
    expect(localizedCount(undefined, ['label'])).toBe(0)
  })
})
