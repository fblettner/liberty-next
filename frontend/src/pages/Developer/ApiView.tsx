// Developer reference → API (C12): every route of the running server — method, path, summary, its
// module, and the access it requires (read from the route's dependencies and the handler's own
// permission checks). Left, the categories and modules with their counts; right, the shared grid; a
// row opens its detail. Modelled on nomaubl's API tab.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import type { ColumnDef } from '@tanstack/react-table'
import { BookOpen, Search } from 'lucide-react'
import { Banner, Button, Centered, Tag, type Tone } from '../../common'
import { DataTable } from '../../common/DataTable'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter } from '../../common/Modal'
import styled from '@emotion/styled'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ApiError, resolveApiPath } from '../../api/client'
import { apiGroups, filterRoutes, getApiReference } from '../../services/devref'
import type { ApiReference, ApiRoute } from '../../types/devref'
import { Doc } from './Doc'
import { Body, Facts, Head, Label, Main, Mono, Side, SideCat, SideItem, SideList, SideSearch } from './styled'

const METHOD_TONE: Record<string, Tone> = { GET: 'green', POST: 'blue', PUT: 'orange', PATCH: 'yellow', DELETE: 'red' }
const ACCESS_TONE: Record<string, Tone> = { public: 'green', 'signed-in': 'neutral', superuser: 'red', permission: 'blue', role: 'purple' }

function AccessTag({ r }: { r: ApiRoute }) {
  const { t } = useTranslation()
  const label = r.access === 'permission' && r.permissions.length ? r.permissions.join(' · ') : t(`developer.access.${r.access}`, { defaultValue: r.access })
  return <Tag $tone={ACCESS_TONE[r.access] ?? 'neutral'}>{label}</Tag>
}

export default function ApiView() {
  const { t } = useTranslation()
  const [ref, setRef] = useState<ApiReference | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [group, setGroup] = useState('')
  const [detail, setDetail] = useState<ApiRoute | null>(null)

  useEffect(() => {
    getApiReference().then(setRef).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  const cats = useMemo(() => apiGroups(ref?.routes ?? []), [ref])
  // Typing searches every route (the selected module is ignored); the left list then shows each
  // module's matches.
  const searching = q.trim() !== ''
  const shown = useMemo(() => filterRoutes(ref?.routes ?? [], q, searching ? '' : group), [ref, q, group, searching])
  const matchCount = useMemo(() => {
    const m = new Map<string, number>()
    if (searching) for (const r of shown) m.set(r.group, (m.get(r.group) ?? 0) + 1)
    return m
  }, [shown, searching])

  const columns = useMemo<ColumnDef<ApiRoute, unknown>[]>(() => [
    { id: 'method', header: t('developer.colMethod'), accessorFn: (r) => r.method, size: 90,
      cell: (c) => <Tag $tone={METHOD_TONE[c.row.original.method] ?? 'neutral'}>{c.row.original.method}</Tag> },
    { id: 'path', header: t('developer.colPath'), accessorFn: (r) => r.path, cell: (c) => <Mono style={{ color: colors.text.primary }}>{c.row.original.path}</Mono> },
    { id: 'summary', header: t('developer.colSummary'), accessorFn: (r) => r.summary },
    { id: 'group', header: t('developer.colGroup'), accessorFn: (r) => r.group },
    { id: 'access', header: t('developer.colAccess'), accessorFn: (r) => `${r.access} ${r.permissions.join(' ')}`, cell: (c) => <AccessTag r={c.row.original} /> },
  ], [t])

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!ref) return <Centered />
  return (
    <>
      <Body>
        <Side>
          <SideSearch>
            <Search size={13} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('developer.apiSearch')} />
          </SideSearch>
          <SideList>
            <SideItem type="button" $on={!searching && group === ''} onClick={() => { setGroup(''); setQ('') }}>
              <span className="name">{t('developer.allRoutes')}</span>
              <span className="n">{searching ? `${shown.length} / ${ref.total}` : ref.total}</span>
            </SideItem>
            {cats.map((c) => (
              <div key={c.category}>
                <SideCat>{c.category}</SideCat>
                {c.groups.map((g) => (
                  <SideItem key={g.group} type="button" $on={!searching && group === g.group} onClick={() => { setGroup(g.group); setQ('') }}
                    style={searching && !matchCount.get(g.group) ? { opacity: 0.45 } : undefined}>
                    <span className="name">{g.group}</span>
                    <span className="n">{searching ? matchCount.get(g.group) ?? 0 : g.n}</span>
                  </SideItem>
                ))}
              </div>
            ))}
          </SideList>
        </Side>
        <Main>
          <Head>
            <span className="title">{searching ? `“${q.trim()}”` : group || 'API'}</span>
            <span className="sub">{t('developer.routesN', { n: shown.length })}</span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              <Button $size="sm" $variant="ghost" onClick={() => window.open(resolveApiPath('/docs'), '_blank', 'noreferrer')}><BookOpen size={13} /> /docs</Button>
              <Button $size="sm" $variant="ghost" onClick={() => window.open(resolveApiPath('/redoc'), '_blank', 'noreferrer')}><BookOpen size={13} /> /redoc</Button>
            </span>
          </Head>
          <DataTable<ApiRoute> columns={columns} data={shown} tableId="developer:api" exportFilename="liberty-api" onRowClick={setDetail} />
        </Main>
      </Body>
      {detail && <RouteDetail r={detail} onClose={() => setDetail(null)} />}
    </>
  )
}

function RouteDetail({ r, onClose }: { r: ApiRoute; onClose: () => void }) {
  const { t } = useTranslation()
  return createPortal(
    <Overlay onClick={onClose}>
      <Modal style={{ width: 'min(820px, 94vw)', maxHeight: '86vh', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader><Tag $tone={METHOD_TONE[r.method] ?? 'neutral'}>{r.method}</Tag> <Mono style={{ marginLeft: 8 }}>{r.path}</Mono></ModalHeader>
        <ModalBody style={{ overflowY: 'auto', fontSize: fontSize.sm }}>
          <div style={{ color: colors.text.primary, marginBottom: 6 }}>{r.summary}</div>
          {r.description && <div style={{ marginBottom: 12 }}><Doc text={r.description} /></div>}
          <Facts>
            <span className="k">{t('developer.colAccess')}</span><span className="v"><AccessTag r={r} /></span>
            <span className="k">{t('developer.colGroup')}</span><span className="v">{r.category} · {r.group}{r.tag ? ` (${r.tag})` : ''}</span>
            <span className="k">{t('developer.handler')}</span><span className="v mono" style={{ color: colors.blue.main }}>{r.file}:{r.line}</span>
            {!r.in_schema && (<><span className="k" /><span className="v" style={{ color: colors.text.muted }}>{t('developer.notInSchema')}</span></>)}
          </Facts>

          {r.parameters.length > 0 && (
            <>
              <Label>{t('developer.params')}</Label>
              <FieldRows rows={r.parameters.map((p) => ({ name: p.name, meta: `${p.in} · ${p.type}`, required: p.required, description: p.description }))} />
            </>
          )}

          <Label>{t('developer.request')}{r.request ? <Mono style={{ fontWeight: 400, marginLeft: 8 }}>{r.request.type} · {r.request.content}</Mono> : null}</Label>
          {r.request ? (
            <>
              {r.request.fields.length > 0 && (
                <FieldRows rows={r.request.fields.map((f) => ({ name: f.name, meta: f.type, required: f.required, description: f.description }))} />
              )}
              {r.request.example !== null && r.request.example !== undefined && <Json value={r.request.example} />}
            </>
          ) : <div style={{ color: colors.text.muted }}>{t('developer.noBody')}</div>}

          <Label>{t('developer.response')}{r.response ? <Mono style={{ fontWeight: 400, marginLeft: 8 }}>{r.response.code} · {r.response.type}</Mono> : null}</Label>
          {r.response
            ? (r.response.example !== null && r.response.example !== undefined ? <Json value={r.response.example} /> : null)
            : <div style={{ color: colors.text.muted }}>{t('developer.responseUndeclared')}</div>}
        </ModalBody>
        <ModalFooter><Button onClick={onClose}>{t('common.close')}</Button></ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}

const Pre = styled.pre`
  margin: 6px 0 0; padding: 10px 12px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.primary}; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 280px; overflow: auto;
  flex-shrink: 0;   /* a flex child of the modal body: without it the block collapses to a line when the body scrolls */
`
const Rows = styled.div`
  display: grid; grid-template-columns: minmax(140px, auto) minmax(120px, auto) minmax(0, 1fr); gap: 4px 14px; font-size: ${fontSize.sm};
  & .n { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & .n .req { color: ${colors.red.main}; margin-left: 2px; }
  & .m { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow-wrap: anywhere; }
  & .d { color: ${colors.text.secondary}; }
`

function FieldRows({ rows }: { rows: { name: string; meta: string; required: boolean; description: string }[] }) {
  return (
    <Rows>
      {rows.map((f) => (
        <div key={f.name} style={{ display: 'contents' }}>
          <span className="n">{f.name}{f.required && <span className="req">*</span>}</span>
          <span className="m">{f.meta}</span>
          <span className="d">{f.description}</span>
        </div>
      ))}
    </Rows>
  )
}

function Json({ value }: { value: unknown }) {
  return <Pre>{JSON.stringify(value, null, 2)}</Pre>
}
