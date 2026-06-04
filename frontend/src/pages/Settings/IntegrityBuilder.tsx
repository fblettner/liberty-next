// Settings → Integrity. Read-only diagnostics: fetches GET /admin/config/integrity (the inverse
// of find-usages — every cross-reference validated) and lists broken references (errors) + smells
// (unused connectors / orphan screens — warnings), grouped by category. Each row deep-links into
// the editor that owns the offender (reusing FindUsagesModal's deepLinkToUrl), so the operator
// jumps straight to the fix. Not an editor itself — nothing to save.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { AlertTriangle, ArrowRight, CheckCircle2, RefreshCw, Search, XCircle } from 'lucide-react'
import { Banner, Button, Centered, Row, SpinnerRing, Stack } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import { deepLinkToUrl } from './FindUsagesModal'

interface Issue {
  severity: 'error' | 'warning'
  category: string
  message: string
  deep_link: Record<string, unknown>
}
interface IntegrityResult { issues: Issue[]; errors: number; warnings: number }

type SeverityFilter = 'all' | 'error' | 'warning'

const Bar = styled(Row)`
  align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 14px;
`
const Counts = styled.div`
  display: inline-flex; align-items: center; gap: 12px; font-size: ${fontSize.sm}; color: ${colors.text.muted};
  & b { font-weight: 600; }
  & .err { color: ${colors.red.main}; }
  & .warn { color: ${colors.orange.main}; }
`
const Chips = styled.div`display: inline-flex; gap: 4px;`
const Chip = styled.button<{ $active?: boolean }>`
  height: 28px; padding: 0 12px; border-radius: ${radius.sm}; cursor: pointer; font-size: ${fontSize.sm};
  font-family: ${fonts.sans};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  &:hover { color: ${colors.text.primary}; }
`
const FilterBox = styled.div`
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px; flex: 1; min-width: 160px; max-width: 320px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  & input { border: none; background: transparent; outline: none; flex: 1; min-width: 0; color: ${colors.text.primary};
    font-size: ${fontSize.sm}; font-family: ${fonts.sans}; }
  & svg { color: ${colors.text.muted}; flex-shrink: 0; }
`
const GroupHead = styled.div`
  display: flex; align-items: center; gap: 8px; margin: 16px 0 6px; font-size: ${fontSize.sm}; font-weight: 600;
  color: ${colors.text.primary};
  & .count { color: ${colors.text.muted}; font-weight: 400; }
`
const IssueRow = styled.div<{ $severity: 'error' | 'warning' }>`
  display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; margin-bottom: 4px;
  & .dot { flex-shrink: 0; color: ${({ $severity }) => ($severity === 'error' ? colors.red.main : (colors.orange.main))}; display: flex; }
  & .msg { flex: 1; min-width: 0; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
`
const GoBtn = styled.button`
  display: inline-flex; align-items: center; gap: 4px; height: 26px; padding: 0 9px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; color: ${colors.text.secondary};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans}; cursor: pointer; flex-shrink: 0;
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.border}; }
  &:disabled { opacity: 0.4; cursor: default; }
`
const Clean = styled.div`
  display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 48px 0; color: ${colors.text.muted};
  & svg { color: ${colors.green.main}; }
`

export default function IntegrityBuilder() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [data, setData] = useState<IntegrityResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sev, setSev] = useState<SeverityFilter>('all')
  const [q, setQ] = useState('')

  const load = () => {
    setBusy(true); setError(null)
    api.get<IntegrityResult>('/admin/config/integrity')
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
      .finally(() => setBusy(false))
  }
  useEffect(load, [t])

  // Filter by severity + free text, then group by category preserving the server's order
  // (errors first). A Map keeps insertion order so groups render errors-first.
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const m = new Map<string, Issue[]>()
    for (const i of data?.issues ?? []) {
      if (sev !== 'all' && i.severity !== sev) continue
      if (needle && !(`${i.category} ${i.message}`.toLowerCase().includes(needle))) continue
      const arr = m.get(i.category) ?? []
      arr.push(i)
      m.set(i.category, arr)
    }
    return [...m.entries()]
  }, [data, sev, q])

  if (error && !data) return <Banner $tone="error">{error}</Banner>
  if (!data) return <Centered />

  const shownCount = groups.reduce((n, [, arr]) => n + arr.length, 0)

  return (
    <div>
      <Bar>
        <Counts>
          <span className="err"><b>{data.errors}</b> {t('settings.integrity.errors', { count: data.errors, defaultValue: 'errors' })}</span>
          <span className="warn"><b>{data.warnings}</b> {t('settings.integrity.warnings', { count: data.warnings, defaultValue: 'warnings' })}</span>
        </Counts>
        <Row gap={8} style={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Chips>
            <Chip $active={sev === 'all'} onClick={() => setSev('all')}>{t('common.all', 'All')}</Chip>
            <Chip $active={sev === 'error'} onClick={() => setSev('error')}>{t('settings.integrity.errorsOnly', 'Errors')}</Chip>
            <Chip $active={sev === 'warning'} onClick={() => setSev('warning')}>{t('settings.integrity.warningsOnly', 'Warnings')}</Chip>
          </Chips>
          <FilterBox>
            <Search size={13} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('settings.integrity.filter', 'Filter…')} />
          </FilterBox>
          <Button $variant="ghost" $size="sm" onClick={load} disabled={busy}>
            {busy ? <SpinnerRing size={13} /> : <RefreshCw size={13} />} {t('settings.integrity.recheck', 'Re-check')}
          </Button>
        </Row>
      </Bar>

      {data.issues.length === 0 ? (
        <Clean>
          <CheckCircle2 size={32} />
          <div>{t('settings.integrity.clean', 'No integrity issues — every reference resolves.')}</div>
        </Clean>
      ) : shownCount === 0 ? (
        <Clean><div>{t('common.noMatches', 'No matches')}</div></Clean>
      ) : (
        <Stack gap={2}>
          {groups.map(([category, arr]) => (
            <div key={category}>
              <GroupHead>
                {arr[0].severity === 'error' ? <XCircle size={14} color={colors.red.main} /> : <AlertTriangle size={14} color={colors.orange.main} />}
                {category} <span className="count">({arr.length})</span>
              </GroupHead>
              {arr.map((i, idx) => {
                const url = deepLinkToUrl(i.deep_link)
                return (
                  <IssueRow key={`${category}:${idx}`} $severity={i.severity}>
                    <span className="dot">{i.severity === 'error' ? <XCircle size={14} /> : <AlertTriangle size={14} />}</span>
                    <span className="msg">{i.message}</span>
                    <GoBtn disabled={!url} onClick={() => url && navigate(url)} title={t('settings.integrity.goto', 'Go to')}>
                      {t('settings.integrity.goto', 'Go to')} <ArrowRight size={12} />
                    </GoBtn>
                  </IssueRow>
                )
              })}
            </div>
          ))}
        </Stack>
      )}
    </div>
  )
}
