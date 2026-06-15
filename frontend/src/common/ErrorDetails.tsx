import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Banner } from './Banner'
import { colors, radius, fontSize, fonts } from '../theme'

/** Reduce a raw backend/DB error to a short, human summary — strips the Python/driver wrapper
 *  noise ("query failed: IntegrityError: (oracledb…)") and cuts the giant ``[SQL: …] [parameters: …]``
 *  dump. Returns the summary plus whether the original carried extra detail worth expanding. */
export function summarizeError(message: string): { summary: string; hasDetails: boolean } {
  const full = (message || '').trim()
  let s = full
  s = s.replace(/^query failed:\s*/i, '')   // Liberty's connector wrapper
  s = s.replace(/^[\w.]*Error:\s*/, '')      // "IntegrityError: "
  s = s.replace(/^\([^)]*\)\s*/, '')         // "(oracledb.exceptions.IntegrityError) "
  const cut = s.search(/\s*\[SQL:/)          // drop the SQL + bound-parameters dump
  if (cut >= 0) s = s.slice(0, cut)
  s = s.trim()
  return { summary: s || full, hasDetails: !!s && s !== full }
}

const Toggle = styled.button`
  align-self: flex-start; background: none; border: none; padding: 0; cursor: pointer;
  font-size: ${fontSize.micro}; color: inherit; text-decoration: underline; opacity: 0.8;
  font-family: ${fonts.sans};
  &:hover { opacity: 1; }
`
const Detail = styled.pre`
  margin: 0; width: 100%; box-sizing: border-box; max-height: 220px; overflow: auto;
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  padding: 8px 10px; font-family: ${fonts.mono}; font-size: ${fontSize.micro};
  line-height: 1.5; color: ${colors.text.secondary}; white-space: pre-wrap; word-break: break-word;
`

/** Error strip that shows a concise summary and, when the original message carried a SQL /
 *  parameter dump (or otherwise differs), a "Show details" toggle revealing the full text in a
 *  scrollable monospace block. Use anywhere a raw backend error would otherwise overflow a Banner. */
export function ErrorDetails(
  { message, tone = 'error' }: { message: string; tone?: 'error' | 'warning' | 'ok' | 'info' },
) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const { summary, hasDetails } = useMemo(() => summarizeError(message), [message])
  return (
    <Banner $tone={tone} style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start', maxWidth: '100%' }}>
      <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{summary}</span>
      {hasDetails && (
        <Toggle type="button" onClick={() => setOpen((o) => !o)}>
          {open ? t('common.hideDetails', 'Hide details') : t('common.showDetails', 'Show details')}
        </Toggle>
      )}
      {open && hasDetails && <Detail>{message}</Detail>}
    </Banner>
  )
}
