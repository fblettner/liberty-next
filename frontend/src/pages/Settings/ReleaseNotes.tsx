// Release notes — the bundled changelog (GET /admin/release-notes → { version, en, fr }). Picks the
// language from the UI (FR falls back to EN), builds a per-version table of contents from the
// "## <version> — <date>" headings, and renders each version section with the shared <Markdown>.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Tag as TagIcon } from 'lucide-react'
import { api } from '../../api/client'
import { Banner, SpinnerRing } from '../../common'
import { Markdown } from '../../common/Markdown'
import { colors, fontSize, fonts, radius } from '../../theme'

interface Comp { component: string; label: string; version: string; en: string; fr: string }
interface Section { version: string; date: string; anchor: string; md: string }

const Wrap = styled.div`display: grid; grid-template-columns: 200px 1fr; gap: 18px; align-items: start;`
const Toc = styled.div`position: sticky; top: 0; display: flex; flex-direction: column; gap: 2px;`
const TocHead = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.05em; padding: 4px 8px;`
const TocItem = styled.button`
  display: flex; align-items: baseline; gap: 6px; text-align: left; padding: 6px 8px; border-radius: ${radius.md};
  border: 1px solid transparent; background: transparent; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  &:hover { background: ${colors.bg.input}; }
  & .v { font-family: ${fonts.mono}; font-weight: 600; }
  & .d { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const Content = styled.div`min-width: 0;`
const SectionBox = styled.section`scroll-margin-top: 8px; padding-bottom: 8px;`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px; text-align: center;`
const CompBtn = styled.button<{ $active: boolean }>`
  display: flex; align-items: baseline; gap: 6px; padding: 5px 12px; border-radius: ${radius.sm}; cursor: pointer; border: none;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${(p) => (p.$active ? colors.bg.card : 'transparent')}; color: ${(p) => (p.$active ? colors.text.primary : colors.text.secondary)};
  box-shadow: ${(p) => (p.$active ? '0 1px 2px rgba(0,0,0,0.12)' : 'none')};
  & .v { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  &:hover { color: ${colors.text.primary}; }
`

const anchorOf = (v: string) => `rn-${v.replace(/[^a-z0-9.]/gi, '-')}`

// Split "## <version> — <date>\n<body>" sections out of the markdown (after the top "# title").
function parseSections(md: string): { intro: string; sections: Section[] } {
  const parts = md.split(/^## /m)
  const intro = parts.shift() ?? ''
  const sections: Section[] = []
  for (const p of parts) {
    const nl = p.indexOf('\n')
    const heading = (nl === -1 ? p : p.slice(0, nl)).trim()
    const m = heading.match(/^(\S+)\s*(?:[—–-]\s*(.+))?$/)
    const version = m?.[1] ?? heading
    sections.push({ version, date: (m?.[2] ?? '').trim(), anchor: anchorOf(version), md: `## ${p}` })
  }
  return { intro, sections }
}

export default function ReleaseNotes() {
  const { t, i18n } = useTranslation()
  const [comps, setComps] = useState<Comp[] | null>(null)
  const [ci, setCi] = useState(0)
  const [err, setErr] = useState<string | null>(null)
  const refs = useRef<Record<string, HTMLElement | null>>({})

  useEffect(() => {
    api.get<{ components: Comp[] }>('/admin/release-notes')
      .then((r) => setComps(r.components))
      .catch((e) => { setErr(e instanceof Error ? e.message : String(e)); setComps([]) })
  }, [])

  const comp = comps?.[ci]
  const md = useMemo(() => {
    if (!comp) return ''
    return (i18n.language?.startsWith('fr') && comp.fr.trim()) ? comp.fr : comp.en
  }, [comp, i18n.language])
  const { intro, sections } = useMemo(() => parseSections(md), [md])

  if (comps === null) return <SpinnerRing />

  return (
    <div>
      {err && <Banner $tone="error">{err}</Banner>}
      {comps.length > 1 && (
        <div style={{ display: 'inline-flex', gap: 2, padding: 3, borderRadius: radius.md, background: colors.bg.input, marginBottom: 14 }}>
          {comps.map((c, i) => (
            <CompBtn key={c.component} $active={i === ci} onClick={() => setCi(i)}>{c.label} <span className="v">{c.version}</span></CompBtn>
          ))}
        </div>
      )}
      {!md.trim() ? (
        <Muted>{t('releaseNotes.empty', 'No release notes bundled with this build.')}</Muted>
      ) : (
        <Wrap>
          <Toc>
            <TocHead>{t('releaseNotes.versions', 'Versions')}</TocHead>
            {sections.map((s) => (
              <TocItem key={s.anchor} onClick={() => refs.current[s.anchor]?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
                <TagIcon size={11} style={{ alignSelf: 'center', color: colors.text.muted }} />
                <span className="v">{s.version}</span>{s.date && <span className="d">{s.date}</span>}
              </TocItem>
            ))}
          </Toc>
          <Content>
            {intro.trim() && <Markdown compact>{intro}</Markdown>}
            {sections.map((s) => (
              <SectionBox key={s.anchor} ref={(el) => { refs.current[s.anchor] = el }}>
                <Markdown compact>{s.md}</Markdown>
              </SectionBox>
            ))}
          </Content>
        </Wrap>
      )}
    </div>
  )
}
