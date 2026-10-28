// Developer reference → Data model (C12): the framework's own tables (from their SQLAlchemy models)
// and each connector's declared tables — the physical table its read query selects from, its key,
// its CRUD queries, the screens / jobs / Explorer models that use it, the related tables, and its
// columns (the screens' column settings with the dictionary's labels). Modelled on nomaubl's Tables tab.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ColumnDef } from '@tanstack/react-table'
import { KeyRound, Search } from 'lucide-react'
import { Banner, Centered, Tag, type Tone } from '../../common'
import { DataTable } from '../../common/DataTable'
import { colors } from '../../theme'
import { ApiError } from '../../api/client'
import { filterTables, getDataModel, tableGroups } from '../../services/devref'
import type { DataColumn, DataModel } from '../../types/devref'
import { Doc } from './Doc'
import { Body, Facts, Head, Main, Mono, Side, SideCat, SideItem, SideList, SideSearch, TAGS_SHOWN, Tags } from './styled'

const USE_TONE: Record<string, Tone> = { screen: 'blue', dashboard: 'green', chart: 'yellow', job: 'orange', explorer: 'purple', framework: 'neutral' }

export default function DataView() {
  const { t } = useTranslation()
  const [model, setModel] = useState<DataModel | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState<string>('')

  useEffect(() => {
    getDataModel().then((m) => { setModel(m); setSelected((s) => s || m.tables[0]?.id || '') })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  const groups = useMemo(() => tableGroups(filterTables(model?.tables ?? [], q)), [model, q])
  const table = model?.tables.find((x) => x.id === selected) ?? null
  // A related table is named without its owner: find it among the same owner's group first.
  const byName = (name: string) => model?.tables.find((x) => x.name === name && x.group === table?.group) ?? model?.tables.find((x) => x.name === name)

  const columns = useMemo<ColumnDef<DataColumn, unknown>[]>(() => [
    { id: 'name', header: t('developer.colColumn'), accessorFn: (c) => c.name,
      cell: (c) => <Mono style={{ color: colors.text.primary, fontWeight: c.row.original.key ? 600 : 400 }}>
        {c.row.original.name}{c.row.original.key && <KeyRound size={11} style={{ marginLeft: 6, color: colors.yellow.main, verticalAlign: -1 }} />}
      </Mono> },
    { id: 'label', header: t('developer.colLabel'), accessorFn: (c) => c.label },
    { id: 'type', header: t('developer.colType'), accessorFn: (c) => c.type, cell: (c) => <Mono>{c.row.original.type}</Mono> },
    { id: 'ref', header: t('developer.colReferences'), accessorFn: (c) => c.references ?? '', cell: (c) => c.row.original.references ? <Mono>→ {c.row.original.references}</Mono> : null },
    { id: 'screens', header: t('developer.colScreens'), accessorFn: (c) => c.screens.join(' '),
      cell: (c) => {
        const s = c.row.original.screens
        return <Tags title={s.join(', ')}>{s.slice(0, TAGS_SHOWN).map((x) => <Tag key={x}>{x}</Tag>)}{s.length > TAGS_SHOWN && <Tag>+{s.length - TAGS_SHOWN}</Tag>}</Tags>
      } },
    { id: 'description', header: t('developer.colDescription'), accessorFn: (c) => c.description },
  ], [t])

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!model) return <Centered />
  return (
    <Body>
      <Side>
        <SideSearch>
          <Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('developer.dataSearch')} />
        </SideSearch>
        <SideList>
          {groups.map((g) => (
            <div key={`${g.owner}:${g.group}`}>
              <SideCat>{g.owner === 'framework' ? `${t('developer.framework')} · ${g.group}` : g.group}</SideCat>
              {g.tables.map((x) => (
                <SideItem key={x.id} type="button" $on={selected === x.id} onClick={() => setSelected(x.id)} style={{ flexWrap: 'wrap' }}>
                  <span className="name">{x.name}</span>
                  <span className="n">{t('developer.columnsN', { n: x.columns.length })}</span>
                  {x.label && <span style={{ width: '100%', fontSize: 12, color: colors.text.muted }}>{x.label}</span>}
                </SideItem>
              ))}
            </div>
          ))}
        </SideList>
      </Side>
      <Main>
        {table && (
          <>
            <Head style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                <span className="title">{table.name}</span>
                {table.label && <span style={{ fontWeight: 600, color: colors.text.primary }}>{table.label}</span>}
              </div>
              {table.related.length > 0 && (
                <Tags style={{ flexWrap: 'wrap' }}>
                  <Tag $tone="blue">{table.name}</Tag>
                  {table.related.map((r) => {
                    const target = byName(r)
                    return <span key={r} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ color: colors.text.muted }}>→</span>
                      <button type="button" onClick={() => target && setSelected(target.id)} disabled={!target}
                        style={{ border: 'none', background: 'none', padding: 0, cursor: target ? 'pointer' : 'default' }}><Tag>{r}</Tag></button>
                    </span>
                  })}
                </Tags>
              )}
              {table.description && <Doc text={table.description} />}
              <Facts>
                <span className="k">{t('developer.physical')}</span><span className="v mono" title={table.physical_raw}>{table.physical || '—'}</span>
                <span className="k">{t('developer.key')}</span><span className="v mono">{table.key.join(' + ') || '—'}</span>
                {table.slots.length > 0 && (<><span className="k">{t('developer.slots')}</span>
                  <span className="v"><Tags>{table.slots.map((s) => <Tag key={s}>{s}</Tag>)}</Tags></span></>)}
                <span className="k">{t('developer.usedBy')}</span>
                <span className="v"><Tags style={{ flexWrap: 'wrap' }}>
                  {table.used_by.length === 0 ? <span style={{ color: colors.text.muted }}>—</span>
                    : table.used_by.map((u) => <Tag key={`${u.kind}:${u.name}`} $tone={USE_TONE[u.kind] ?? 'neutral'}>{u.kind} · {u.name}</Tag>)}
                </Tags></span>
              </Facts>
            </Head>
            <DataTable<DataColumn> columns={columns} data={table.columns} tableId="developer:data" exportFilename={`liberty-${table.name}`} />
          </>
        )}
      </Main>
    </Body>
  )
}
