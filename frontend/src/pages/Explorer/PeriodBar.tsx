// The period bar: which date field the period applies to (the primary entity's default date
// first) and the shared DateRangeFilter (presets, custom range). "All dates" means no period.
import { useTranslation } from 'react-i18next'
import { CalendarRange } from 'lucide-react'
import { DateRangeFilter, SearchSelect, type DatePreset, type DateRange } from '../../common'
import type { ExplorerField, PeriodDef } from '../../types/explorer'
import { ShelfBox, ShelfTitle } from './shelfKit'


export function PeriodBar({ period, dateFields, onChange }: {
  period: PeriodDef | null
  dateFields: ExplorerField[]
  onChange: (next: PeriodDef | null) => void
}) {
  const { t } = useTranslation()
  if (dateFields.length === 0) return null
  const field = period?.field ?? dateFields[0].key
  const preset: DatePreset = period?.preset ?? 'all'
  const range: DateRange = period?.range ?? { dateFrom: '', dateTo: '' }
  return (
    <ShelfBox $tone="purple" data-shelf="period">
      <ShelfTitle $tone="purple"><span className="chip"><CalendarRange size={13} /></span>{t('explorer.period')}</ShelfTitle>
      <div style={{ width: 220 }}>
        <SearchSelect value={field} options={dateFields.map((f) => ({ value: f.key, label: f.label, mono: f.key }))} onChange={(v) => onChange({ field: v, preset, range })} />
      </div>
      <DateRangeFilter preset={preset} range={range} onChange={(p, r) => onChange(p === 'all' ? null : { field, preset: p, range: r })} />
    </ShelfBox>
  )
}
