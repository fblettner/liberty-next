// i18next bootstrap — English + French, language persisted to localStorage.
// Imported for its side effect from main.tsx; the active language is switched
// from the Layout's utility bar (see components/Layout.tsx).
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en'
import fr from './locales/fr'

export const LANGUAGES = ['en', 'fr'] as const
export type Language = (typeof LANGUAGES)[number]
export const LANGUAGE_KEY = 'liberty.language'

const stored = (() => {
  try {
    const v = localStorage.getItem(LANGUAGE_KEY)
    return v === 'en' || v === 'fr' ? v : null
  } catch {
    return null
  }
})()

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, fr: { translation: fr } },
  lng: stored ?? 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

export default i18n
