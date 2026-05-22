// nomaflow schedule editor (NOMAFLOW-UI.md §3.2 / increment 6). Presets + a raw cron
// field + a live "next fires" preview. The preview comes from GET /admin/jobs/cron-preview
// — APScheduler's own cron parser server-side, so the preview can't disagree with what
// the scheduler will actually do.
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { CalendarClock } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Input, Button } from '../../common'
import { colors, fontSize, fonts } from '../../theme'

const Wrap = styled.div`display: flex; flex-direction: column; gap: 6px;`
const Label = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const Presets = styled.div`display: flex; gap: 6px; flex-wrap: wrap;`
const Preview = styled.div`
  display: flex; flex-direction: column; gap: 2px; padding: 8px 10px;
  border: 1px solid ${colors.border}; border-radius: 8px; background: ${colors.bg.input};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
`
const PreviewHead = styled.div`display: flex; align-items: center; gap: 6px; color: ${colors.text.muted};`
const FireLine = styled.span`color: ${colors.text.secondary};`
const ErrLine = styled.span`color: ${colors.red.main};`
const Hint = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted};`

// Preset id → the cron it generates (null = manual-only, no schedule).
const PRESETS: { id: string; cron: string | null }[] = [
  { id: 'manual', cron: null },
  { id: 'hourly', cron: '0 * * * *' },
  { id: 'daily', cron: '0 2 * * *' },
  { id: 'weekly', cron: '0 2 * * 1' },
  { id: 'monthly', cron: '0 2 1 * *' },
]

export default function ScheduleField({ value, timezone, onChange }: {
  value: string | null
  timezone: string | null
  onChange: (cron: string | null) => void
}) {
  const { t } = useTranslation()
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

  const activePreset = PRESETS.find((p) => p.cron === (value ?? null))?.id

  return (
    <Wrap>
      <Label>{t('nomaflow.editor.fieldSchedule')}</Label>
      <Presets>
        {PRESETS.map((p) => (
          <Button
            key={p.id}
            type="button"
            $variant={activePreset === p.id ? 'primary' : 'ghost'}
            $size="sm"
            onClick={() => onChange(p.cron)}
          >
            {t(`nomaflow.schedule.${p.id}`)}
          </Button>
        ))}
      </Presets>
      <Input
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder="30 2 * * *"
      />
      {!value && <Hint>{t('nomaflow.schedule.manualHint')}</Hint>}
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
