// A time zone, picked from the ones the browser knows (Europe/Paris, America/New_York…) instead of
// typed from memory; another name can still be typed.
import { useMemo } from 'react'
import { SearchSelect, type SearchSelectOption } from './SearchSelect'

/** The time zones this browser knows; a short list where it cannot say. */
export function knownTimezones(): string[] {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.('timeZone')
    if (list && list.length) return list.includes('UTC') ? list : ['UTC', ...list]
  } catch { /* an old browser: the short list below */ }
  return ['UTC', 'Europe/Paris', 'Europe/London', 'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney']
}

export function TimezoneSelect({ value, onChange, placeholder }: {
  value: string
  /** '' when cleared: the default applies (the placeholder says which). */
  onChange: (zone: string) => void
  placeholder?: string
}) {
  const options = useMemo<SearchSelectOption[]>(() => knownTimezones().map((z) => ({ value: z, label: z })), [])
  return <SearchSelect sort clearable allowCustom value={value} onChange={onChange} options={options} placeholder={placeholder} />
}
