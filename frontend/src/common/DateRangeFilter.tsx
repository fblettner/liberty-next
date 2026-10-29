// The period control (port of nomaubl's): one button showing the range ("1 – 30 Sept 2026")
// and, when the page passes one, the date basis; it opens a panel with the presets as a list,
// a two-month calendar for a custom range, and the basis picker in the footer. Dashboards,
// lists and the explorer share it. Pure date logic lives in services/period.ts.
import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Calendar, ChevronDown } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import { PortalPanel } from './PortalPanel'
import { MonthGrid } from './MonthGrid'
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
  /** Fill the container's width like an input (a field in a filter form), the chevron at the end. */
  block?: boolean
}

const Trigger = styled.button<{ $open: boolean; $block?: boolean }>`
  display: inline-flex; align-items: center; gap: 8px; height: 34px; padding: 0 10px 0 12px;
  ${({ $block }) => ($block ? 'width: 100%; min-width: 0; height: 32px; & > svg:last-child { margin-left: auto; }' : '')}   /* an input's size */
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


export function DateRangeFilter({ preset, range, onChange, basis, block }: Props) {
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
      <Trigger ref={triggerRef} type="button" $open={open} $block={block} onClick={openPanel} aria-haspopup="dialog" aria-expanded={open}>
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
                <MonthGrid year={left.y} month={left.m} from={pending.dateFrom} to={pending.dateTo} today={today} lang={lang} onPick={pickDay} nav={{ prev: () => shift(-1) }}
                  onJump={(y, m) => { const d = new Date(y, m + 1, 1); setView({ y: d.getFullYear(), m: d.getMonth() }) }} />
                <MonthGrid year={view.y} month={view.m} from={pending.dateFrom} to={pending.dateTo} today={today} lang={lang} onPick={pickDay} nav={{ next: () => shift(1) }}
                  onJump={(y, m) => setView({ y, m })} />
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
