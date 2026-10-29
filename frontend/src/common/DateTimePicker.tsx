// A date and a time, themed: the DatePicker for the day and a time field beside it (hh:mm or
// hh:mm:ss, checked when the field is left or on Enter — an impossible time goes back to the
// value). The value is ``yyyy-mm-dd hh:mm:ss`` ('' when cleared); a zoned value shows in local
// time. For ``datetime`` / ``timestamp`` fields of the record form, which edited the day only.
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { colors, fontSize, fonts, radius, shadow } from '../theme'
import { DatePicker } from './DatePicker'
import { joinDateTime, normaliseTime, splitDateTime } from '../services/dateTime'

const Row = styled.div`display: flex; gap: 6px; width: 100%; min-width: 0;`
const Day = styled.div`flex: 1; min-width: 0;`
const Time = styled.input`
  width: 96px; flex-shrink: 0; height: 32px; box-sizing: border-box; padding: 0 10px; outline: none;
  border-radius: ${radius.md}; border: 1px solid ${colors.border}; background: ${colors.bg.input}; color: ${colors.text.primary};
  font-size: ${fontSize.base}; font-family: ${fonts.sans}; font-variant-numeric: tabular-nums;
  transition: border-color 0.15s, box-shadow 0.15s;
  &:focus { border-color: ${colors.blue.main}; box-shadow: ${shadow.focus}; }
  &::placeholder { color: ${colors.text.muted}; }
  &:disabled, &:read-only { background: var(--bg-readonly); color: ${colors.text.muted}; }
`

export function DateTimePicker({ value, onChange, required, disabled }: {
  /** A date-time as the row holds it (ISO, ``yyyy-mm-dd hh:mm:ss``, possibly zoned); '' when empty. */
  value: string
  /** ``yyyy-mm-dd hh:mm:ss``, '' when the day is cleared. */
  onChange: (v: string) => void
  required?: boolean
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const { date, time } = splitDateTime(value)
  const [draft, setDraft] = useState(time)
  useEffect(() => { setDraft(time) }, [time])

  const commitTime = () => {
    const v = draft.trim()
    if (v === time) return
    const n = v === '' ? '00:00:00' : normaliseTime(v)
    if (n === null || !date) { setDraft(time); return }   // an impossible time, or no day yet
    onChange(joinDateTime(date, n))
  }

  return (
    <Row>
      <Day><DatePicker value={date} required={required} disabled={disabled} onChange={(d) => onChange(joinDateTime(d, time))} /></Day>
      <Time value={draft} placeholder={t('common.timePlaceholder')} aria-label={t('common.time')} inputMode="numeric" disabled={disabled || !date}
        onChange={(e) => setDraft(e.target.value)} onBlur={commitTime}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitTime() } }} />
    </Row>
  )
}
