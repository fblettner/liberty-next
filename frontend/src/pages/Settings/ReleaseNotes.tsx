// Release notes — the bundled changelog (GET /admin/release-notes → { version, en, fr }), read one version
// at a time: the versions in a panel on the left, the picked version beside it with each of its entries as a
// card titled by the sentence the entry opens on. A search finds an entry by its words across every version.
// The language follows the UI (FR falls back to EN). The parsing is in services/releaseNotes.ts, tested.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Search, Tag as TagIcon, X } from 'lucide-react'
import { api } from '../../api/client'
import { Banner, Card, SpinnerRing } from '../../common'
import { Tag } from '../../common/Tag'
import { Markdown } from '../../common/Markdown'
import { colors, fontSize, fonts, radius } from '../../theme'
import { matchingItems, parseItems, parseSections, type NoteItem } from '../../services/releaseNotes'

interface Comp { component: string; label: string; version: string; en: string; fr: string }

const Wrap = styled.div`display: grid; grid-template-columns: minmax(220px, 280px) minmax(0, 1fr); gap: 14px; align-items: start;`
const Versions = styled(Card)`
  position: sticky; top: 0; display: flex; flex-direction: column; gap: 4px; padding: 10px; max-height: calc(100vh - 250px); overflow-y: auto;
`
const VersionLine = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 8px; text-align: left; padding: 7px 10px; border-radius: ${radius.md}; cursor: pointer; flex-shrink: 0;
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : colors.border)}; background: ${(p) => (p.$on ? colors.blue.bg : colors.bg.input)};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  & .v { font-family: ${fonts.mono}; font-weight: 600; }
  & .d { flex: 1; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  & .n { flex-shrink: 0; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  &:hover { border-color: ${(p) => (p.$on ? colors.blue.main : colors.blue.border)}; }
`
const Side = styled(Card)`padding: 0; min-width: 0; overflow: hidden;`
const Head = styled.div`
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 14px; border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input};
  & .title { font-family: ${fonts.mono}; font-weight: 600; color: ${colors.text.primary}; }
  & .count { color: ${colors.text.muted}; font-size: ${fontSize.sm}; flex: 1; }
`
const SearchBox = styled.div`
  display: flex; align-items: center; gap: 6px; height: 30px; padding: 0 9px; width: min(320px, 100%);
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; color: ${colors.text.muted};
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
  & button { display: inline-flex; padding: 0; border: none; background: none; color: ${colors.text.muted}; cursor: pointer; }
`
const Items = styled.div`padding: 14px; display: flex; flex-direction: column; gap: 10px;`
const Item = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; padding: 10px 14px;
  & .title { display: flex; align-items: baseline; gap: 8px; font-family: ${fonts.sans}; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; }
  & .title + .body { margin-top: 4px; }
  & .body { color: ${colors.text.secondary}; }
`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px; text-align: center;`
const CompBtn = styled.button<{ $active: boolean }>`
  display: flex; align-items: baseline; gap: 6px; padding: 5px 12px; border-radius: ${radius.sm}; cursor: pointer; border: none;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${(p) => (p.$active ? colors.bg.card : 'transparent')}; color: ${(p) => (p.$active ? colors.text.primary : colors.text.secondary)};
  & .v { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  &:hover { color: ${colors.text.primary}; }
`

export default function ReleaseNotes() {
  const { t, i18n } = useTranslation()
  const [comps, setComps] = useState<Comp[] | null>(null)
  const [ci, setCi] = useState(0)
  const [picked, setPicked] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [err, setErr] = useState<string | null>(null)

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
  // every version with its entries (the heading itself is not an entry)
  const versions = useMemo(() => parseSections(md).sections.map((s) => ({ ...s, items: parseItems(s.md) })), [md])
  const current = versions.find((v) => v.version === picked) ?? versions[0]
  const searching = q.trim() !== ''
  // while searching: the entries of EVERY version that hold the words, each saying its version
  const found = useMemo(
    () => (searching ? versions.flatMap((v) => matchingItems(v.items, q).map((item) => ({ item, version: v.version }))) : []),
    [versions, q, searching],
  )

  if (comps === null) return <SpinnerRing />

  const card = (item: NoteItem, key: string, version?: string) => (
    <Item key={key} data-note>
      {(item.title || version) && (
        <div className="title">{version && <Tag $tone="blue">{version}</Tag>}<span>{item.title}</span></div>
      )}
      {item.body && <div className="body"><Markdown compact>{item.body}</Markdown></div>}
    </Item>
  )

  return (
    <div>
      {err && <Banner $tone="error">{err}</Banner>}
      {comps.length > 1 && (
        <div style={{ display: 'inline-flex', gap: 2, padding: 3, borderRadius: radius.md, background: colors.bg.input, marginBottom: 14 }}>
          {comps.map((c, i) => (
            <CompBtn key={c.component} $active={i === ci} onClick={() => { setCi(i); setPicked(null) }}>{c.label} <span className="v">{c.version}</span></CompBtn>
          ))}
        </div>
      )}
      {!md.trim() || !current ? (
        <Muted>{t('releaseNotes.empty')}</Muted>
      ) : (
        <Wrap>
          <Versions>
            {versions.map((v) => (
              <VersionLine key={v.anchor} type="button" $on={!searching && v.version === current.version} data-version={v.version}
                onClick={() => { setPicked(v.version); setQ('') }}>
                <TagIcon size={12} />
                <span className="v">{v.version}</span><span className="d">{v.date}</span><span className="n">{v.items.length}</span>
              </VersionLine>
            ))}
          </Versions>
          <Side>
            <Head>
              <span className="title">{searching ? t('releaseNotes.found', { n: found.length }) : current.version}</span>
              <span className="count">{searching ? '' : [current.date, t('releaseNotes.entries', { n: current.items.length })].filter(Boolean).join(' · ')}</span>
              <SearchBox>
                <Search size={13} />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('releaseNotes.search')} />
                {q && <button type="button" title={t('common.clear')} onClick={() => setQ('')}><X size={12} /></button>}
              </SearchBox>
            </Head>
            <Items>
              {searching
                ? (found.length ? found.map((f, i) => card(f.item, `${f.version}:${i}`, f.version)) : <Muted>{t('releaseNotes.noMatch')}</Muted>)
                : current.items.map((item, i) => card(item, `${current.version}:${i}`))}
            </Items>
          </Side>
        </Wrap>
      )}
    </div>
  )
}
