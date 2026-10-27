// The period control (port of nomaubl's): one button showing the range ("1 – 30 Sept 2026")
// and, when the page passes one, the date basis; it opens a panel with the presets as a list,
// a two-month calendar for a custom range, and the basis picker in the footer. Dashboards,
// lists and the explorer share it. Pure date logic lives in services/period.ts.
import { useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Calendar, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import { PortalPanel } from './PortalPanel'
import { DATE_PRESETS, EMPTY_RANGE, formatRange, fromIso, rangeForPreset, toIso, type DatePreset, type DateRange } from '../services/period'

export type { DatePreset, DateRange }

export interface DateBasis {
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}

interface Props {
  preset: DatePreset
  range: DateRange
  onChange: (preset: DatePreset, range: DateRange) => void
  /** Which date the period compares against, chosen in the panel's footer. */
  basis?: DateBasis
}

const Trigger = styled.button<{ $open: boolean }>`
  display: inline-flex; align-items: center; gap: 8px; height: 34px; padding: 0 10px 0 12px;
  border-radius: ${radius.md}; border: 1px solid ${({ $open }) => ($open ? colors.blue.border : colors.border)};
  background: ${colors.bg.input}; color: ${colors.text.primary}; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; white-space: nowrap;
  & .muted { color: ${colors.text.muted}; }
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover { border-color: ${colors.blue.border}; }
`
const Body = styled.div`display: flex; min-width: 0;`
const Presets = styled.div`
  width: 176px; flex-shrink: 0; padding: 8px; border-right: 1px solid ${colors.border};
  display: flex; flex-direction: column; gap: 1px;
`
const PresetBtn = styled.button<{ $active?: boolean }>`
  text-align: left; height: 32px; padding: 0 10px; border: none; border-radius: ${radius.md}; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
  &:hover { background: ${({ $active }) => ($active ? colors.blue.bg : 'var(--hover-subtle)')}; color: ${colors.text.primary}; }
`
const Calendars = styled.div`display: flex; gap: 24px; padding: 14px 18px; min-width: 0;`
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
const Footer = styled.div`
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 14px; border-top: 1px solid ${colors.border};
`
const FooterGroup = styled.div`display: flex; flex-wrap: wrap; align-items: center; gap: 14px; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const Segments = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const Segment = styled.button<{ $active?: boolean }>`
  height: 28px; padding: 0 10px; border: none; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
`
const Actions = styled.div`display: flex; gap: 8px; margin-left: auto;`
const Btn = styled.button<{ $primary?: boolean }>`
  height: 30px; padding: 0 14px; border-radius: ${radius.md}; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  border: 1px solid ${({ $primary }) => ($primary ? colors.blue.main : colors.border)};
  background: ${({ $primary }) => ($primary ? colors.blue.main : 'transparent')};
  color: ${({ $primary }) => ($primary ? colors.text.inverse : colors.text.primary)};
  font-weight: ${({ $primary }) => ($primary ? 600 : 400)};
  &:disabled { opacity: 0.45; cursor: default; }
`
const Hint = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans};`

function MonthGrid({ year, month, from, to, today, lang, onPick, nav }: {
  year: number; month: number; from: string; to: string; today: string; lang: string
  onPick: (iso: string) => void
  nav?: { prev?: () => void; next?: () => void }
}) {
  const first = new Date(year, month, 1)
  const offset = (first.getDay() + 6) % 7 // weeks start on Monday
  const days = new Date(year, month + 1, 0).getDate()
  const dows = useMemo(() => {
    const f = new Intl.DateTimeFormat(lang, { weekday: 'narrow' })
    return Array.from({ length: 7 }, (_, i) => f.format(new Date(2024, 0, 1 + i))) // 1 Jan 2024 was a Monday
  }, [lang])
  const title = new Intl.DateTimeFormat(lang, { month: 'long', year: 'numeric' }).format(first)
  const cells: Array<string | null> = [...Array<null>(offset).fill(null)]
  for (let d = 1; d <= days; d++) cells.push(toIso(new Date(year, month, d)))
  return (
    <Month>
      <MonthHead>
        {nav?.prev ? <NavBtn type="button" aria-label="Previous month" onClick={nav.prev}><ChevronLeft size={16} /></NavBtn> : <span style={{ width: 28 }} />}
        <span>{title}</span>
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

export function DateRangeFilter({ preset, range, onChange, basis }: Props) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<DateRange>(EMPTY_RANGE)      // the custom range being picked
  const [view, setView] = useState(() => { const d = new Date(); return { y: d.getFullYear(), m: d.getMonth() } })
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const panelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  const openPanel = () => {
    setPending(preset === 'all' ? EMPTY_RANGE : range)
    const anchor = range.dateTo ? fromIso(range.dateTo) : new Date()
    setView({ y: anchor.getFullYear(), m: anchor.getMonth() })
    setOpen((o) => !o)
  }
  const selectPreset = (p: DatePreset) => { setOpen(false); onChange(p, rangeForPreset(p)) }
  const pickDay = (iso: string) => setPending((prev) => {
    if (!prev.dateFrom || prev.dateTo) return { dateFrom: iso, dateTo: '' }
    return iso < prev.dateFrom ? { dateFrom: iso, dateTo: prev.dateFrom } : { dateFrom: prev.dateFrom, dateTo: iso }
  })
  const applyCustom = () => {
    if (!pending.dateFrom) return
    setOpen(false)
    onChange('custom', { dateFrom: pending.dateFrom, dateTo: pending.dateTo || pending.dateFrom })
  }

  const today = toIso(new Date())
  const left = { y: view.m === 0 ? view.y - 1 : view.y, m: (view.m + 11) % 12 }
  const shift = (n: number) => setView((v) => { const d = new Date(v.y, v.m + n, 1); return { y: d.getFullYear(), m: d.getMonth() } })
  const label = preset === 'all' ? t('period.all') : formatRange(range, lang)
  const basisLabel = basis?.options.find((o) => o.value === basis.value)?.label

  return (
    <>
      <Trigger ref={triggerRef} type="button" $open={open} onClick={openPanel} aria-haspopup="dialog" aria-expanded={open}>
        <Calendar size={14} />
        <span>{label || t('period.custom')}</span>
        {basisLabel && <span className="muted">{basisLabel}</span>}
        <ChevronDown size={14} />
      </Trigger>
      {open && (
        <PortalPanel triggerRef={triggerRef} panelRef={panelRef} maxWidth={760} minWidth={300} roomBelow={420} onDismiss={() => setOpen(false)}>
          <div role="dialog" aria-label={t('period.label')}>
            <Body>
              <Presets>
                {DATE_PRESETS.map((p) => (
                  <PresetBtn key={p} type="button" $active={preset === p} onClick={() => selectPreset(p)}>{t(`period.${p}`)}</PresetBtn>
                ))}
              </Presets>
              <Calendars>
                <MonthGrid year={left.y} month={left.m} from={pending.dateFrom} to={pending.dateTo} today={today} lang={lang} onPick={pickDay} nav={{ prev: () => shift(-1) }} />
                <MonthGrid year={view.y} month={view.m} from={pending.dateFrom} to={pending.dateTo} today={today} lang={lang} onPick={pickDay} nav={{ next: () => shift(1) }} />
              </Calendars>
            </Body>
            <Footer>
              <FooterGroup>
                {basis ? (
                  <>
                    <span>{t('period.basis')}</span>
                    <Segments>
                      {basis.options.map((o) => (
                        <Segment key={o.value} type="button" $active={o.value === basis.value} onClick={() => basis.onChange(o.value)}>{o.label}</Segment>
                      ))}
                    </Segments>
                  </>
                ) : (
                  <Hint>{pending.dateFrom && !pending.dateTo ? t('period.pickEnd') : t('period.pickHint')}</Hint>
                )}
              </FooterGroup>
              <Actions>
                <Btn type="button" onClick={() => setOpen(false)}>{t('common.cancel')}</Btn>
                <Btn type="button" $primary disabled={!pending.dateFrom} onClick={applyCustom}>{t('period.apply')}</Btn>
              </Actions>
            </Footer>
          </div>
        </PortalPanel>
      )}
    </>
  )
}
