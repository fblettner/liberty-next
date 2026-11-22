// A table of a database, picked: the tables (and views) of the connector's schema are read once
// and offered; a name can still be typed — a table that is not there yet, or a database that
// cannot be read. Used wherever a table was a typed name: a job's copy and load steps, an
// Explorer entity read from a table.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Input } from './Input'
import { SearchSelect, type SearchSelectOption } from './SearchSelect'
import { getPoolSchemaTables } from '../services/poolSchema'

export function TableSelect({ connector, schema, value, onChange, disabled }: {
  /** The connector whose database is read; none: the field waits for it. */
  connector: string | null
  /** The schema the tables are listed from; blank: the connector's own. */
  schema?: string | null
  value: string
  onChange: (table: string) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const [options, setOptions] = useState<SearchSelectOption[] | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!connector) { setOptions(null); return }
    let cancelled = false
    setLoading(true)
    getPoolSchemaTables(connector, schema?.trim() || null)
      .then((found) => {
        if (cancelled) return
        setOptions((found?.tables ?? []).map((tb) => ({
          value: tb.name, label: tb.kind === 'view' ? t('tableSelect.view') : t('tableSelect.columns', { count: tb.columns.length }), mono: tb.name,
        })))
      })
      .catch(() => { if (!cancelled) setOptions([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [connector, schema, t])
  if (!connector) return <Input value={value} disabled placeholder={t('tableSelect.pickConnector')} />
  // nothing to offer (the database cannot be read, the schema is empty): the name is typed
  if (!loading && (!options || options.length === 0)) {
    return <Input value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} placeholder={t('tableSelect.typed')} />
  }
  return (
    <SearchSelect sort clearable allowCustom value={value} onChange={onChange} options={options ?? []} disabled={disabled}
      placeholder={loading ? t('tableSelect.loading') : t('tableSelect.pick')} />
  )
}
