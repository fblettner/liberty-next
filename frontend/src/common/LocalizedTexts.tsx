// "In the other languages": the texts of a record said in each language besides the one the
// configuration is written in. One line per language, one field per text; a blank field means
// "show the record's own text". The value is the record's `l` table (services/localized.ts).
//
// The languages offered are the interface's own, plus any the record already carries — so a
// translation written by hand in a language the interface does not have is still shown and kept.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { api } from '../api/client'
import { Input, Textarea } from './Input'
import { colors, fontSize, fonts, radius } from '../theme'
import { localizedValue, otherLanguages, prunedLocalized, withLocalized, type Localized } from '../services/localized'

export type LocalizedField = {
  /** The record's field (`label`, `description`, `sub`…). */
  key: string
  /** What the editor calls it — the same words as the field itself. */
  label: string
  /** The record's own text: shown greyed in the blank field, as what will be displayed. */
  base?: string
  multiline?: boolean
}

const Box = styled.div`
  border: 1px dashed ${colors.border}; border-radius: ${radius.md}; padding: 8px 10px; margin-top: 4px;
`
const Head = styled.div`
  display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px;
  font-family: ${fonts.sans}; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.muted};
  & .hint { font-weight: 400; letter-spacing: 0; text-transform: none; }
`
const Line = styled.div`
  display: grid; grid-template-columns: 96px 1fr; gap: 8px; align-items: start; margin-bottom: 6px;
  & .lang { font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; padding-top: 22px; text-transform: capitalize; }
`
const Fields = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px;`
const Cell = styled.label`
  display: flex; flex-direction: column; gap: 3px; min-width: 0;
  & .name { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`

// The language the configuration is written in: the server's default (asked once per page load).
let written: Promise<string> | null = null
const writtenLanguage = (): Promise<string> => {
  written ??= api.get<{ dictionary?: { default_language?: string } }>('/info')
    .then((i) => i.dictionary?.default_language || 'en')
    .catch(() => 'en')
  return written
}

/** A language's name, said in the interface's language ("français", "German"). */
export const languageName = (language: string, shown: string): string => {
  try { return new Intl.DisplayNames([shown], { type: 'language' }).of(language) || language } catch { return language }
}

/** The languages a text may be given in besides the one the configuration is written in: the
 *  interface's own, plus *present* — those a record already carries. */
export function useOtherLanguages(present: readonly string[] = []): string[] {
  const { i18n } = useTranslation()
  const [base, setBase] = useState('en')
  useEffect(() => { let on = true; void writtenLanguage().then((l) => { if (on) setBase(l) }); return () => { on = false } }, [])
  return otherLanguages([...Object.keys(i18n.options.resources ?? {}), ...present], base)
}

export function LocalizedTexts({ value, onChange, fields }: {
  value: Localized | null | undefined
  /** The new table — undefined once nothing is said in another language. */
  onChange: (next: Localized | undefined) => void
  fields: LocalizedField[]
}) {
  const { t, i18n } = useTranslation()
  const offered = useOtherLanguages(Object.keys(value ?? {}))
  if (!offered.length || !fields.length) return null
  return (
    <Box>
      <Head>{t('common.otherLanguages')} <span className="hint">{t('common.otherLanguagesHint')}</span></Head>
      {offered.map((lang) => (
        <Line key={lang}>
          <span className="lang">{languageName(lang, i18n.language)}</span>
          <Fields>
            {fields.map((f) => {
              const set = (v: string) => onChange(prunedLocalized(withLocalized(value, lang, f.key, v)))
              return (
                <Cell key={f.key}>
                  <span className="name">{f.label}</span>
                  {f.multiline
                    ? <Textarea rows={4} value={localizedValue(value, lang, f.key)} placeholder={f.base} onChange={(e) => set(e.target.value)} />
                    : <Input value={localizedValue(value, lang, f.key)} placeholder={f.base} onChange={(e) => set(e.target.value)} />}
                </Cell>
              )
            })}
          </Fields>
        </Line>
      ))}
    </Box>
  )
}
