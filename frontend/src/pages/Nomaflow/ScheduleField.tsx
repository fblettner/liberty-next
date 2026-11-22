// nomaflow schedule editor (NOMAFLOW-UI.md §3.2 / increment 6). The schedule said in words — how often,
// then only what that choice needs (services/schedulePlan.ts); an expression typed for anything else —
// and a live "next fires" preview. The preview comes from GET /admin/jobs/cron-preview
// — APScheduler's own cron parser server-side, so the preview can't disagree with what
// the scheduler will actually do.
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { CalendarClock } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Input, SearchSelect, type SearchSelectOption } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { describeCron, weekdayName } from '../../services/cronText'
import { PLAN_KINDS, cronToPlan, planToCron, startPlan, type PlanKind, type SchedulePlan } from '../../services/schedulePlan'

const Wrap = styled.div`display: flex; flex-direction: column; gap: 6px;`
const Label = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const Preview = styled.div`
  display: flex; flex-direction: column; gap: 2px; padding: 8px 10px;
  border: 1px solid ${colors.border}; border-radius: 8px; background: ${colors.bg.input};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
`
const PreviewHead = styled.div`display: flex; align-items: center; gap: 6px; color: ${colors.text.muted};`
const FireLine = styled.span`color: ${colors.text.secondary};`
const ErrLine = styled.span`color: ${colors.red.main};`
const Hint = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted};`

const PlanRow = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const DayBtn = styled.button<{ $on: boolean }>`
  padding: 4px 9px; border-radius: ${radius.md}; cursor: pointer; font-size: ${fontSize.sm}; font-family: inherit;
  border: 1px solid ${(p) => (p.$on ? colors.blue.border : colors.border)};
  background: ${(p) => (p.$on ? colors.blue.bg : 'transparent')};
  color: ${(p) => (p.$on ? colors.blue.main : colors.text.secondary)};
`
// the days of the week in the order they are read (Monday first), as the scheduler numbers them
const WEEK = [1, 2, 3, 4, 5, 6, 0]

export default function ScheduleField({ value, timezone, onChange }: {
  value: string | null
  timezone: string | null
  onChange: (cron: string | null) => void
}) {
  const { t, i18n } = useTranslation()
  const [fires, setFires] = useState<string[]>([])
  const [err, setErr] = useState<string | null>(null)

  // Debounced preview — re-query 400ms after the cron stops changing.
  useEffect(() => {
    const cron = (value ?? '').trim()
    if (!cron) { setFires([]); setErr(null); return }
    const handle = setTimeout(() => {
      const qs = new URLSearchParams({ schedule: cron, count: '5' })
      if (timezone) qs.set('timezone', timezone)
      api.get<{ next: string[] }>(`/admin/jobs/cron-preview?${qs}`)
        .then((r) => { setFires(r.next); setErr(null) })
        .catch((e) => {
          setFires([])
          setErr(e instanceof ApiError ? e.message : String(e))
        })
    }, 400)
    return () => clearTimeout(handle)
  }, [value, timezone])

  // The schedule as a person says it (services/schedulePlan.ts): how often, then only what that
  // choice needs. "Something else" keeps the expression itself, typed — and stays picked while it
  // is being typed, even when what is typed happens to be one of the plans.
  const [typed, setTyped] = useState(() => cronToPlan(value).kind === 'custom')
  const plan: SchedulePlan = typed ? { kind: 'custom', cron: value ?? '' } : cronToPlan(value)
  const set = (next: SchedulePlan) => onChange(planToCron(next))
  const kindOpts: SearchSelectOption[] = PLAN_KINDS.map((k) => ({ value: k, label: t(`nomaflow.schedule.plan.${k}`) }))
  const said = describeCron(value, i18n.language)
  const num = (text: string, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.trunc(Number(text) || lo)))

  return (
    <Wrap data-schedule>
      <Label>{t('nomaflow.editor.fieldSchedule')}</Label>
      <PlanRow>
        <div style={{ minWidth: 220 }} data-schedule-kind>
          <SearchSelect value={plan.kind} options={kindOpts}
            onChange={(k) => { const kind = (k || 'manual') as PlanKind; setTyped(kind === 'custom'); set(startPlan(kind, plan)) }} />
        </div>
        {plan.kind === 'minutes' && (
          <>
            <Input type="number" min={1} max={59} style={{ width: 80 }} value={plan.every} onChange={(e) => set({ ...plan, every: num(e.target.value, 1, 59) })} />
            <span>{t('nomaflow.schedule.unitMinutes')}</span>
          </>
        )}
        {plan.kind === 'hours' && (
          <>
            <Input type="number" min={1} max={23} style={{ width: 80 }} value={plan.every} onChange={(e) => set({ ...plan, every: num(e.target.value, 1, 23) })} />
            <span>{t('nomaflow.schedule.unitHoursAt')}</span>
            <Input type="number" min={0} max={59} style={{ width: 80 }} value={plan.minute} onChange={(e) => set({ ...plan, minute: num(e.target.value, 0, 59) })} />
            <span>{t('nomaflow.schedule.unitPast')}</span>
          </>
        )}
        {plan.kind === 'month' && (
          <>
            <span>{t('nomaflow.schedule.onThe')}</span>
            <Input type="number" min={1} max={31} style={{ width: 80 }} value={plan.day} onChange={(e) => set({ ...plan, day: num(e.target.value, 1, 31) })} />
            <span>{t('nomaflow.schedule.at')}</span>
            <Input type="time" style={{ width: 130 }} value={plan.time} onChange={(e) => set({ ...plan, time: e.target.value || '02:00' })} />
          </>
        )}
        {plan.kind === 'days' && (
          <>
            <span>{t('nomaflow.schedule.at')}</span>
            <Input type="time" style={{ width: 130 }} value={plan.time} onChange={(e) => set({ ...plan, time: e.target.value || '02:00' })} />
          </>
        )}
      </PlanRow>
      {plan.kind === 'days' && (
        <PlanRow data-schedule-days>
          {WEEK.map((d) => (
            <DayBtn key={d} type="button" $on={plan.days.includes(d)} aria-pressed={plan.days.includes(d)}
              onClick={() => set({ ...plan, days: plan.days.includes(d) ? plan.days.filter((x) => x !== d) : [...plan.days, d] })}>
              {weekdayName(d, i18n.language)}
            </DayBtn>
          ))}
          <Hint>{plan.days.length === 0 ? t('nomaflow.schedule.everyDay') : ''}</Hint>
        </PlanRow>
      )}
      {plan.kind === 'custom' && (
        <>
          <Input value={value ?? ''} onChange={(e) => onChange(e.target.value || null)} placeholder="0 8-18 * * 1-5" />
          <Hint>{t('nomaflow.schedule.customHint')}</Hint>
        </>
      )}
      {!value && <Hint>{t('nomaflow.schedule.manualHint')}</Hint>}
      {value && said && <Hint data-schedule-said>{t(said.key, said.values)}</Hint>}
      {value && (
        <Preview>
          <PreviewHead><CalendarClock size={13} /> {t('nomaflow.schedule.nextFires')}</PreviewHead>
          {err && <ErrLine>{err}</ErrLine>}
          {!err && fires.length === 0 && <FireLine>{t('nomaflow.schedule.computing')}</FireLine>}
          {!err && fires.map((f) => (
            <FireLine key={f}>{new Date(f).toLocaleString()}</FireLine>
          ))}
        </Preview>
      )}
    </Wrap>
  )
}
