// Reports → the documentation reports (C12, superuser): the implementation report (this install) or the
// technical report (the release). Left, the settings (language, the cover's date label); right, the
// sections to include with their options; below, the download as PDF or Markdown. Modelled on nomaubl's
// Reports page.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Download, FileText } from 'lucide-react'
import { apiDownload, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, Checkbox, Input } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { defaultSections, docReportPath } from '../../services/docReports'
import type { DocReport, DocReportKind } from '../../types/docReports'

const Grid = styled.div`display: grid; grid-template-columns: minmax(260px, 420px) minmax(0, 1fr); gap: 16px; align-items: start;
  @media (max-width: 900px) { grid-template-columns: 1fr; }`
const Box = styled(Card)`display: flex; flex-direction: column; gap: 14px;`
const BoxTitle = styled.div`display: flex; align-items: baseline; gap: 12px; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary};
  & .count { margin-left: auto; font-weight: 400; font-size: ${fontSize.sm}; color: ${colors.text.muted}; }`
const Muted = styled.div`color: ${colors.text.secondary}; font-size: ${fontSize.sm}; line-height: 1.5;`
const Label = styled.div`font-size: ${fontSize.micro}; letter-spacing: 0.06em; text-transform: uppercase; color: ${colors.text.muted}; margin-bottom: 6px;`
const Segmented = styled.div`display: inline-flex; gap: 2px; padding: 3px; border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: ${colors.bg.input};`
const Seg = styled.button<{ $on: boolean }>`padding: 5px 14px; border: none; border-radius: ${radius.md}; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${({ $on }) => ($on ? colors.blue.bg : 'transparent')}; color: ${({ $on }) => ($on ? colors.blue.main : colors.text.secondary)};
  font-weight: ${({ $on }) => ($on ? 600 : 400)}; &:hover { color: ${colors.text.primary}; }`
const LinkBtn = styled.button`border: none; background: none; padding: 0; cursor: pointer; color: ${colors.blue.main}; font-family: ${fonts.sans}; font-size: ${fontSize.sm};`
const Tiles = styled.div`display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px;`
const Tile = styled.div<{ $on: boolean }>`display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; border-radius: ${radius.lg};
  border: 1px solid ${({ $on }) => ($on ? `color-mix(in srgb, ${colors.blue.main} 45%, transparent)` : colors.border)};
  background: ${({ $on }) => ($on ? `color-mix(in srgb, ${colors.blue.main} 7%, ${colors.bg.card})` : colors.bg.card)};
  & .title { font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.sm}; }`
const Bar = styled(Card)`display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 16px;`
const Done = styled.span`color: ${colors.green.main}; font-size: ${fontSize.sm};`

export default function DocReportView({ kind, report }: { kind: DocReportKind; report: DocReport | undefined }) {
  const { t, i18n } = useTranslation()
  const [lang, setLang] = useState<'fr' | 'en'>(i18n.language?.startsWith('en') ? 'en' : 'fr')
  const [date, setDate] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const [options, setOptions] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState<'pdf' | 'markdown' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => { if (report) { setPicked(defaultSections(report)); setOptions({}); setDone(false); setError(null) } }, [report])
  const optionsOf = useMemo(() => {
    const m = new Map<string, DocReport['options']>()
    for (const o of report?.options ?? []) m.set(o.section, [...(m.get(o.section) ?? []), o])
    return m
  }, [report])

  if (!report) return <Centered />
  const toggle = (id: string, on: boolean) => setPicked((p) => (on ? [...p, id] : p.filter((x) => x !== id)))
  const download = async (format: 'pdf' | 'markdown') => {
    setBusy(format); setError(null); setDone(false)
    try {
      await apiDownload(docReportPath(kind, report, { lang, format, sections: picked, date, options }), `liberty-${kind}.${format === 'pdf' ? 'pdf' : 'md'}`)
      setDone(true)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <Grid>
        <Box>
          <BoxTitle>{t('docReports.settings')}</BoxTitle>
          <Muted>{report.subtitle}</Muted>
          <div>
            <Label>{t('docReports.language')}</Label>
            <Segmented role="tablist">
              <Seg type="button" $on={lang === 'fr'} onClick={() => setLang('fr')}>Français</Seg>
              <Seg type="button" $on={lang === 'en'} onClick={() => setLang('en')}>English</Seg>
            </Segmented>
          </div>
          <div>
            <Label>{t('docReports.dateLabel')}</Label>
            <Input value={date} onChange={(e) => setDate(e.target.value)} placeholder={t('docReports.datePlaceholder')} />
          </div>
        </Box>
        <Box>
          <BoxTitle>
            {t('docReports.content')}
            <span className="count">{t('docReports.sectionsOf', { n: picked.length, total: report.sections.length })}</span>
            <LinkBtn type="button" onClick={() => setPicked(report.sections.map((s) => s.id))}>{t('docReports.all')}</LinkBtn>
            <LinkBtn type="button" onClick={() => setPicked([])}>{t('docReports.none')}</LinkBtn>
          </BoxTitle>
          <Tiles>
            {report.sections.map((s) => {
              const on = picked.includes(s.id)
              return (
                <Tile key={s.id} $on={on}>
                  <Checkbox checked={on} onChange={(v) => toggle(s.id, v)} label={<span className="title">{s.title}</span>} />
                  <Muted>{s.description}</Muted>
                  {(optionsOf.get(s.id) ?? []).map((o) => (
                    <Checkbox key={o.id} checked={!!options[o.id]} disabled={!on} label={o.label}
                      onChange={(v) => setOptions((x) => ({ ...x, [o.id]: v }))} />
                  ))}
                </Tile>
              )
            })}
          </Tiles>
        </Box>
      </Grid>
      <Bar>
        <Button $variant="primary" disabled={!picked.length || busy !== null} onClick={() => void download('pdf')}>
          <FileText size={14} /> {busy === 'pdf' ? t('docReports.building') : t('docReports.downloadPdf')}
        </Button>
        <Button $variant="ghost" disabled={!picked.length || busy !== null} onClick={() => void download('markdown')}>
          <Download size={14} /> {busy === 'markdown' ? t('docReports.building') : 'Markdown'}
        </Button>
        {done && <Done>{t('docReports.done')}</Done>}
      </Bar>
      {error && <Banner $tone="error" style={{ marginTop: 12 }}>{error}</Banner>}
    </>
  )
}
