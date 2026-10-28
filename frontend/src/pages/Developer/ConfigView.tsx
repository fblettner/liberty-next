// Developer reference → Configuration (C12): every key of app.toml with where a screen edits it (or
// why it stays in the file) and the code that reads it. Left, the sections with their gaps; right,
// the shared grid; a row opens its detail.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import type { ColumnDef } from '@tanstack/react-table'
import { Search } from 'lucide-react'
import { Banner, Button, Centered, SearchSelect, Tag } from '../../common'
import { DataTable } from '../../common/DataTable'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter } from '../../common/Modal'
import { colors, fontSize } from '../../theme'
import { ApiError } from '../../api/client'
import { filterKeys, getConfigReference, keyLabel, keyStatus, readerModules, sectionCounts, statusCounts, type KeyStatus } from '../../services/devref'
import type { ConfigKey, ConfigReference } from '../../types/devref'
import { Doc } from './Doc'
import { Body, Facts, Head, Label, Main, Mono, RefRow, Side, SideItem, SideList, SideSearch, TAGS_SHOWN, Tags } from './styled'

const ALL = ''

export default function ConfigView() {
  const { t } = useTranslation()
  const [ref, setRef] = useState<ConfigReference | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [section, setSection] = useState(ALL)
  const [status, setStatus] = useState<KeyStatus | ''>('')
  const [detail, setDetail] = useState<ConfigKey | null>(null)

  useEffect(() => {
    getConfigReference().then(setRef).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  const sections = useMemo(() => sectionCounts(ref?.keys ?? []), [ref])
  // Typing searches every section (the selected one is ignored); the left list shows each section's matches.
  const searching = q.trim() !== ''
  const shown = useMemo(() => filterKeys(ref?.keys ?? [], q, searching ? ALL : section, status, (k) => keyLabel(k, t)), [ref, q, section, status, t, searching])
  // The header's counts: the keys in view (the section, or the search) before the status filter.
  const counts = useMemo(() => statusCounts(filterKeys(ref?.keys ?? [], q, searching ? ALL : section, '', (k) => keyLabel(k, t))), [ref, q, section, t, searching])
  const matchCount = useMemo(() => {
    const m = new Map<string, number>()
    if (searching) for (const k of shown) m.set(k.section, (m.get(k.section) ?? 0) + 1)
    return m
  }, [shown, searching])

  const columns = useMemo<ColumnDef<ConfigKey, unknown>[]>(() => [
    { id: 'key', header: t('developer.colKey'), accessorFn: (k) => k.key, cell: (c) => <Mono style={{ color: colors.text.primary }}>{c.row.original.key}</Mono> },
    { id: 'label', header: t('developer.colLabel'), accessorFn: (k) => keyLabel(k, t) },
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

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!ref) return <Centered />

  return (
    <>
      <Body>
        <Side>
          <SideSearch>
            <Search size={13} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('developer.search')} />
          </SideSearch>
          <SideList>
            <SideItem type="button" $on={!searching && section === ALL} onClick={() => { setSection(ALL); setQ('') }}>
              <span className="name">{t('developer.allSections')}</span>
              <span className="n">{searching ? `${shown.length} / ${ref.total}` : ref.total}</span>
              {ref.gaps.length > 0 && <span className="gaps">{t('developer.gapsN', { n: ref.gaps.length })}</span>}
            </SideItem>
            {sections.map((s) => (
              <SideItem key={s.section} type="button" $on={!searching && section === s.section} onClick={() => { setSection(s.section); setQ('') }}
                style={searching && !matchCount.get(s.section) ? { opacity: 0.45 } : undefined}>
                <span className="name">{s.section}</span>
                <span className="n">{searching ? matchCount.get(s.section) ?? 0 : s.total}</span>
                {s.gaps > 0 && <span className="gaps">{t('developer.gapsN', { n: s.gaps })}</span>}
              </SideItem>
            ))}
          </SideList>
        </Side>
        <Main>
          <Head>
            <span className="title">{searching ? `“${q.trim()}”` : section === ALL ? 'app.toml' : `[${section}]`}</span>
            <span className="sub">{t('developer.summaryStatus', { editor: counts.editor, file: counts.file, gaps: counts.gap })}</span>
            <span style={{ marginLeft: 'auto', width: 240 }}>
              <SearchSelect size="sm" value={status} onChange={(v) => setStatus(v as KeyStatus | '')} anyLabel={t('developer.statusAll')}
                options={[
                  { value: 'editor', label: t('developer.statusEditor') },
                  { value: 'file', label: t('developer.fileOnly') },
                  { value: 'gap', label: t('developer.gap') },
                ]} />
            </span>
          </Head>
          <DataTable<ConfigKey> columns={columns} data={shown} tableId="developer:config" exportFilename="liberty-settings"
            onRowClick={setDetail} />
        </Main>
      </Body>
      {detail && <KeyDetail k={detail} onClose={() => setDetail(null)} />}
    </>
  )
}

function KeyDetail({ k, onClose }: { k: ConfigKey; onClose: () => void }) {
  const { t } = useTranslation()
  const status = keyStatus(k)
  return createPortal(
    <Overlay onClick={onClose}>
      <Modal style={{ width: 'min(900px, 94vw)', maxHeight: '86vh', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader><Mono>{k.key}</Mono>{keyLabel(k, t) && <span style={{ fontWeight: 400, color: colors.text.secondary, marginLeft: 10 }}>{keyLabel(k, t)}</span>}</ModalHeader>
        <ModalBody style={{ overflowY: 'auto', fontSize: fontSize.sm }}>
          {k.description && <div style={{ marginBottom: 10 }}><Doc text={k.description} /></div>}
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
