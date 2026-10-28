// Developer reference (C12) — what the running code is made of, as documentation (nothing is edited
// here). First view, Configuration: every key of app.toml with where a screen edits it (or why it
// stays in the file) and the code that reads it. Left, the sections with their gaps; right, the
// shared grid; a row opens its detail. Modelled on nomaubl's Developer reference page.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import type { ColumnDef } from '@tanstack/react-table'
import { Code2, Search } from 'lucide-react'
import { Banner, Button, Card, Centered, Checkbox, PageLayout, Tag } from '../../common'
import { DataTable } from '../../common/DataTable'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter } from '../../common/Modal'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ApiError } from '../../api/client'
import { filterKeys, getConfigReference, keyStatus, readerModules, sectionCounts } from '../../services/devref'
import type { ConfigKey, ConfigReference } from '../../types/devref'

const Body = styled.div`display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 14px; flex: 1; min-height: 0;`
const Side = styled(Card)`padding: 0; display: flex; flex-direction: column; min-height: 0; overflow: hidden;`
const SideSearch = styled.div`
  position: relative; padding: 10px; border-bottom: 1px solid ${colors.border};
  & svg { position: absolute; left: 20px; top: 20px; color: ${colors.text.muted}; }
  & input { width: 100%; box-sizing: border-box; padding: 7px 10px 7px 30px; border-radius: ${radius.md}; border: 1px solid ${colors.border};
    background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; outline: none; }
  & input:focus { border-color: ${colors.blue.border}; }
`
const SideList = styled.div`overflow-y: auto; flex: 1; min-height: 0;`
const SideItem = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 9px 14px; border: none; border-bottom: 1px solid ${colors.border};
  background: ${({ $on }) => ($on ? colors.blue.bg : 'transparent')}; cursor: pointer; text-align: left;
  border-left: 3px solid ${({ $on }) => ($on ? colors.blue.main : 'transparent')};
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; flex: 1; }
  & .n { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .gaps { font-size: ${fontSize.micro}; color: ${colors.red.main}; }
  &:hover { background: var(--hover-subtle); }
`
const Main = styled.div`display: flex; flex-direction: column; gap: 12px; min-height: 0;`
const Head = styled(Card)`display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  & .title { font-family: ${fonts.mono}; font-size: ${fontSize.lg}; font-weight: 600; color: ${colors.text.primary}; }
  & .sub { color: ${colors.text.muted}; font-size: ${fontSize.sm}; }`
const Mono = styled.span`font-family: ${fonts.mono};`
const Tags = styled.span`display: inline-flex; gap: 4px; flex-wrap: nowrap; overflow: hidden; max-width: 100%;`
const TAGS_SHOWN = 3
const Facts = styled.div`
  display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 6px 14px; align-items: baseline; font-size: ${fontSize.sm};
  & .k { color: ${colors.text.muted}; }
  & .v { color: ${colors.text.primary}; overflow-wrap: anywhere; }
  & .mono { font-family: ${fonts.mono}; }
`
const RefRow = styled.div`
  display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 14px; padding: 6px 0; border-bottom: 1px solid ${colors.border};
  &:last-child { border-bottom: none; }
  & .file { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.blue.main}; }
  & .code { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.secondary}; white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 2px; }
`
const Label = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-weight: 600; margin: 14px 0 4px; padding-bottom: 4px; border-bottom: 1px solid ${colors.border};`

const ALL = ''

export default function DeveloperPage() {
  const { t } = useTranslation()
  const [ref, setRef] = useState<ConfigReference | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [section, setSection] = useState(ALL)
  const [onlyGaps, setOnlyGaps] = useState(false)
  const [detail, setDetail] = useState<ConfigKey | null>(null)

  useEffect(() => {
    getConfigReference().then(setRef).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  const sections = useMemo(() => sectionCounts(ref?.keys ?? []), [ref])
  const shown = useMemo(() => filterKeys(ref?.keys ?? [], q, section, onlyGaps), [ref, q, section, onlyGaps])

  const columns = useMemo<ColumnDef<ConfigKey, unknown>[]>(() => [
    { id: 'key', header: t('developer.colKey'), accessorFn: (k) => k.key, cell: (c) => <Mono style={{ color: colors.text.primary }}>{c.row.original.key}</Mono> },
    { id: 'type', header: t('developer.colType'), accessorFn: (k) => k.type, cell: (c) => <Mono>{c.row.original.type}</Mono> },
    {
      id: 'edited', header: t('developer.colEdited'),
      accessorFn: (k) => k.editor ?? (k.file_only ? t('developer.fileOnly') : t('developer.gap')),
      cell: (c) => {
        const k = c.row.original
        const s = keyStatus(k)
        return s === 'editor' ? <span>{k.editor}</span>
          : s === 'file' ? <Tag $tone="purple">{t('developer.fileOnly')}</Tag>
          : <Tag $tone="red">{t('developer.gap')}</Tag>
      },
    },
    {
      id: 'read', header: t('developer.colRead'), accessorFn: (k) => readerModules(k).join(' '),
      cell: (c) => {
        const mods = readerModules(c.row.original)
        return (
          <Tags title={mods.join(', ')}>
            {mods.slice(0, TAGS_SHOWN).map((m) => <Tag key={m}>{m}</Tag>)}
            {mods.length > TAGS_SHOWN && <Tag>+{mods.length - TAGS_SHOWN}</Tag>}
          </Tags>
        )
      },
    },
    { id: 'default', header: t('developer.default'), accessorFn: (k) => (k.default === null || k.default === undefined ? '' : JSON.stringify(k.default)) },
  ], [t])

  if (error) return <PageLayout icon={<Code2 size={18} />} title={t('developer.title')}><Banner $tone="error">{error}</Banner></PageLayout>
  if (!ref) return <PageLayout icon={<Code2 size={18} />} title={t('developer.title')}><Centered /></PageLayout>
  const current = sections.find((s) => s.section === section)

  return (
    <PageLayout icon={<Code2 size={18} />} title={t('developer.title')} description={t('developer.configIntro')}
      headerRight={<Tag $tone="blue">{t('developer.configTitle')}</Tag>}>
      <Body>
        <Side>
          <SideSearch>
            <Search size={13} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('developer.search')} />
          </SideSearch>
          <SideList>
            <SideItem type="button" $on={section === ALL} onClick={() => setSection(ALL)}>
              <span className="name">{t('developer.allSections')}</span>
              <span className="n">{ref.total}</span>
              {ref.gaps.length > 0 && <span className="gaps">{t('developer.gapsN', { n: ref.gaps.length })}</span>}
            </SideItem>
            {sections.map((s) => (
              <SideItem key={s.section} type="button" $on={section === s.section} onClick={() => setSection(s.section)}>
                <span className="name">{s.section}</span>
                <span className="n">{s.total}</span>
                {s.gaps > 0 && <span className="gaps">{t('developer.gapsN', { n: s.gaps })}</span>}
              </SideItem>
            ))}
          </SideList>
        </Side>
        <Main>
          <Head>
            <span className="title">{section === ALL ? 'app.toml' : `[${section}]`}</span>
            <span className="sub">{t('developer.summary', {
              editable: current ? current.total - current.gaps : ref.editable,
              total: current ? current.total : ref.total,
              gaps: current ? current.gaps : ref.gaps.length,
            })}</span>
            <span style={{ marginLeft: 'auto' }}><Checkbox checked={onlyGaps} onChange={setOnlyGaps} label={t('developer.onlyGaps')} /></span>
          </Head>
          <DataTable<ConfigKey> columns={columns} data={shown} tableId="developer:config" exportFilename="liberty-settings"
            onRowClick={setDetail} />
        </Main>
      </Body>
      {detail && <KeyDetail k={detail} onClose={() => setDetail(null)} />}
    </PageLayout>
  )
}

function KeyDetail({ k, onClose }: { k: ConfigKey; onClose: () => void }) {
  const { t } = useTranslation()
  const status = keyStatus(k)
  return createPortal(
    <Overlay onClick={onClose}>
      <Modal style={{ width: 'min(900px, 94vw)', maxHeight: '86vh', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader><Mono>{k.key}</Mono></ModalHeader>
        <ModalBody style={{ overflowY: 'auto', fontSize: fontSize.sm }}>
          {k.description && <div style={{ color: colors.text.secondary, marginBottom: 10 }}>{k.description}</div>}
          <Facts>
            <span className="k">{t('developer.colType')}</span><span className="v mono">{k.type}</span>
            <span className="k">{t('developer.default')}</span>
            <span className="v mono">{k.default === null || k.default === undefined ? t('developer.none') : JSON.stringify(k.default)}</span>
            <span className="k">{t('developer.colEdited')}</span>
            <span className="v">
              {status === 'editor' && k.editor}
              {status === 'file' && <span style={{ color: colors.purple.main }}>{t('developer.fileOnly')} — {k.file_only}</span>}
              {status === 'gap' && <Tag $tone="red">{t('developer.gap')}</Tag>}
            </span>
          </Facts>
          <Label>{t('developer.readByN', { n: k.readers.length })}</Label>
          {k.readers.length === 0 && <div>{t('developer.noReader')}</div>}
          {k.readers.map((r, i) => (
            <RefRow key={i}>
              <span><Tag>{readerModules({ ...k, readers: [r] })[0]}</Tag></span>
              <div>
                <div className="file">{r.file}:{r.line}{r.via ? <span style={{ color: colors.text.muted }}> · {r.via}</span> : null}</div>
                <div className="code">{r.snippet}</div>
              </div>
            </RefRow>
          ))}
        </ModalBody>
        <ModalFooter><Button onClick={onClose}>{t('common.close')}</Button></ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}
