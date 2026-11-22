// THE picker of a function shipped by an application (`module:function`): the shared dropdown,
// its choices grouped by application and module — each line the function's name and what it
// does — and, under it, what is stored and its description in full. One component for every
// place a function is picked, so the list reads the same everywhere. A name the list does not
// hold can still be typed (a function outside the `j_*` convention): the line under says so.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { SearchSelect } from './SearchSelect'
import { callableOptions, loadCallables, type CallableDef } from '../services/callables'
import { colors, fontSize, fonts } from '../theme'

const Under = styled.div`
  margin-top: 4px; font-size: ${fontSize.micro}; font-family: ${fonts.sans}; color: ${colors.text.muted}; line-height: 1.5;
  & .mono { font-family: ${fonts.mono}; color: ${colors.text.secondary}; overflow-wrap: anywhere; }
  & .warn { color: ${colors.yellow.main}; }
`

export function CallableSelect({ value, onChange, placeholder, disabled, clearable }: {
  value: string
  onChange: (callable: string) => void
  placeholder?: string
  disabled?: boolean
  /** An × that empties the field (where no function is a valid answer). */
  clearable?: boolean
}) {
  const { t } = useTranslation()
  const [catalog, setCatalog] = useState<CallableDef[] | null>(null)
  useEffect(() => {
    let on = true
    loadCallables().then((list) => { if (on) setCatalog(list) }).catch(() => { if (on) setCatalog([]) })
    return () => { on = false }
  }, [])
  const options = useMemo(() => callableOptions(catalog ?? [], value), [catalog, value])
  const picked = options.find((o) => o.value === value)
  return (
    <div data-callable-select>
      <SearchSelect
        value={value}
        onChange={onChange}
        options={options}
        allowCustom
        clearable={clearable}
        disabled={disabled}
        loading={catalog === null}
        placeholder={placeholder ?? t('common.callablePick')}
      />
      {value && catalog !== null && (
        <Under>
          <span className="mono">{value}</span>
          {picked
            ? (picked.label !== picked.mono ? <> — {picked.label}</> : null)
            : catalog.length > 0 ? <span className="warn"> — {t('common.callableUnknown')}</span> : null}
        </Under>
      )}
    </div>
  )
}
