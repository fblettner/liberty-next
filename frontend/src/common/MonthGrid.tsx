// One month of the date pickers' calendar (Monday first): the day cells, the edges of a range
// (``from`` / ``to``, the same day for a single date) highlighted, today ringed, optional
// previous / next arrows. With ``onJump`` the title opens a month / year chooser (the 12 months of a
// year, the year by arrows or from 12 at a time) — a date years away is a few clicks, not one per
// month. Shared by DateRangeFilter (a period) and DatePicker (one date).
import { useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import { toIso } from '../services/period'

const Month = styled.div`width: 238px; display: flex; flex-direction: column; gap: 8px;`
const MonthHead = styled.div`
  display: flex; align-items: center; justify-content: space-between; height: 28px;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary};
  text-transform: capitalize;
`
const NavBtn = styled.button`
  width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center;
  border: none; border-radius: ${radius.md}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const Grid = styled.div`display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); row-gap: 2px; text-align: center;`
const Dow = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; padding-bottom: 4px; font-family: ${fonts.sans};`
const Day = styled.button<{ $edge?: boolean; $in?: boolean; $today?: boolean }>`
  height: 32px; border: none; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  font-variant-numeric: tabular-nums;
  border-radius: ${({ $edge, $today }) => ($edge || $today ? radius.md : '0')};
  background: ${({ $edge, $in }) => ($edge ? colors.blue.main : $in ? colors.blue.bg : 'transparent')};
  color: ${({ $edge, $in }) => ($edge ? colors.text.inverse : $in ? colors.text.primary : colors.text.secondary)};
  font-weight: ${({ $edge }) => ($edge ? 600 : 400)};
  box-shadow: ${({ $today, $edge }) => ($today && !$edge ? `inset 0 0 0 1px ${colors.border}` : 'none')};
  &:hover { background: ${({ $edge }) => ($edge ? colors.blue.main : 'var(--hover-subtle)')}; color: ${({ $edge }) => ($edge ? colors.text.inverse : colors.text.primary)}; }
`

const TitleBtn = styled.button`
  height: 28px; padding: 0 8px; border: none; border-radius: ${radius.md}; background: transparent; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; text-transform: capitalize;
  &:hover { background: var(--hover-subtle); }
`
const Choices = styled.div`display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; padding-top: 4px;`
const Choice = styled.button<{ $active?: boolean; $current?: boolean }>`
  height: 36px; border: none; border-radius: ${radius.md}; cursor: pointer; text-transform: capitalize;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-variant-numeric: tabular-nums;
  background: ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  color: ${({ $active }) => ($active ? colors.text.inverse : colors.text.secondary)};
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
  box-shadow: ${({ $current, $active }) => ($current && !$active ? `inset 0 0 0 1px ${colors.border}` : 'none')};
  &:hover { background: ${({ $active }) => ($active ? colors.blue.main : 'var(--hover-subtle)')}; color: ${({ $active }) => ($active ? colors.text.inverse : colors.text.primary)}; }
`

export function MonthGrid({ year, month, from, to, today, lang, onPick, nav, onJump }: {
  year: number; month: number; from: string; to: string; today: string; lang: string
  onPick: (iso: string) => void
  nav?: { prev?: () => void; next?: () => void }
  /** Show another month (the title's chooser); without it the title is plain text. */
  onJump?: (year: number, month: number) => void
}) {
  // the chooser: ``months`` (the 12 months of ``pickYear``) or ``years`` (12 years from ``yearsFrom``)
  const [mode, setMode] = useState<'days' | 'months' | 'years'>('days')
  const [pickYear, setPickYear] = useState(year)
  const [yearsFrom, setYearsFrom] = useState(year - (year % 12))
  const first = new Date(year, month, 1)
  const offset = (first.getDay() + 6) % 7 // weeks start on Monday
  const days = new Date(year, month + 1, 0).getDate()
  const dows = useMemo(() => {
    const f = new Intl.DateTimeFormat(lang, { weekday: 'narrow' })
    return Array.from({ length: 7 }, (_, i) => f.format(new Date(2024, 0, 1 + i))) // 1 Jan 2024 was a Monday
  }, [lang])
  const monthNames = useMemo(() => {
    const f = new Intl.DateTimeFormat(lang, { month: 'short' })
    return Array.from({ length: 12 }, (_, i) => f.format(new Date(2024, i, 1)))
  }, [lang])
  const title = new Intl.DateTimeFormat(lang, { month: 'long', year: 'numeric' }).format(first)
  const cells: Array<string | null> = [...Array<null>(offset).fill(null)]
  for (let d = 1; d <= days; d++) cells.push(toIso(new Date(year, month, d)))
  const thisYear = Number(today.slice(0, 4)), thisMonth = Number(today.slice(5, 7)) - 1
  const openChooser = () => { setPickYear(year); setYearsFrom(year - (year % 12)); setMode('months') }

  if (onJump && mode === 'months') {
    return (
      <Month>
        <MonthHead>
          <NavBtn type="button" aria-label="Previous year" onClick={() => setPickYear((y) => y - 1)}><ChevronLeft size={16} /></NavBtn>
          <TitleBtn type="button" aria-label="Choose the year" onClick={() => { setYearsFrom(pickYear - (pickYear % 12)); setMode('years') }}>{pickYear}</TitleBtn>
          <NavBtn type="button" aria-label="Next year" onClick={() => setPickYear((y) => y + 1)}><ChevronRight size={16} /></NavBtn>
        </MonthHead>
        <Choices>
          {monthNames.map((name, m) => (
            <Choice key={m} type="button" $active={pickYear === year && m === month} $current={pickYear === thisYear && m === thisMonth}
              onClick={() => { setMode('days'); onJump(pickYear, m) }}>{name}</Choice>
          ))}
        </Choices>
      </Month>
    )
  }
  if (onJump && mode === 'years') {
    return (
      <Month>
        <MonthHead>
          <NavBtn type="button" aria-label="Previous years" onClick={() => setYearsFrom((y) => y - 12)}><ChevronLeft size={16} /></NavBtn>
          <span>{yearsFrom} – {yearsFrom + 11}</span>
          <NavBtn type="button" aria-label="Next years" onClick={() => setYearsFrom((y) => y + 12)}><ChevronRight size={16} /></NavBtn>
        </MonthHead>
        <Choices>
          {Array.from({ length: 12 }, (_, i) => yearsFrom + i).map((y) => (
            <Choice key={y} type="button" $active={y === pickYear} $current={y === thisYear}
              onClick={() => { setPickYear(y); setMode('months') }}>{y}</Choice>
          ))}
        </Choices>
      </Month>
    )
  }
  return (
    <Month>
      <MonthHead>
        {nav?.prev ? <NavBtn type="button" aria-label="Previous month" onClick={nav.prev}><ChevronLeft size={16} /></NavBtn> : <span style={{ width: 28 }} />}
        {onJump ? <TitleBtn type="button" aria-label="Choose the month and year" onClick={openChooser}>{title}</TitleBtn> : <span>{title}</span>}
        {nav?.next ? <NavBtn type="button" aria-label="Next month" onClick={nav.next}><ChevronRight size={16} /></NavBtn> : <span style={{ width: 28 }} />}
      </MonthHead>
      <Grid>
        {dows.map((d, i) => <Dow key={i}>{d}</Dow>)}
        {cells.map((iso, i) => iso === null ? <span key={i} /> : (
          <Day key={iso} type="button" onClick={() => onPick(iso)}
            $edge={iso === from || iso === to} $in={!!from && !!to && iso > from && iso < to} $today={iso === today}>
            {Number(iso.slice(8))}
          </Day>
        ))}
      </Grid>
    </Month>
  )
}
