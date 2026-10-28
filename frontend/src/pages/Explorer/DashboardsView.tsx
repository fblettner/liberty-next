// Dashboards of analyses: a 4-column grid of tiles, each running one saved analysis in its own
// view (table / pivot / chart / KPI / detail), with the dashboard's period overriding the
// analyses that have a period field. Edit-layout mode moves, resizes, removes and adds tiles.
// A tile's pencil opens its analysis in the editor; a drill from a tile does the same with the
// cell's values as filters.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, Pencil, Trash2, CalendarRange, Maximize2, Minimize2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, DateRangeFilter, SearchSelect, SpinnerRing, type DatePreset, type DateRange } from '../../common'
import { buildAggregateRequest, buildDetailRequest, drillCellsOf } from '../../services/explorer'
import { TILE_HEIGHT, withDashboardPeriod, type DashboardDefinition, type DashboardTile, type SavedAnalysis, type TileHeight } from '../../services/explorerSaved'
import type { AggregateResult, AnalysisDefinition, DetailResult, DrillCell } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ResultTable } from './ResultTable'
import { PivotView } from './PivotView'
import { ChartView } from './ChartView'

const Grid = styled.div`display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; align-content: start; @media (max-width: 1100px) { grid-template-columns: repeat(2, 1fr); }`
const Card = styled.div<{ $w: number; $h: number }>`
  grid-column: span ${({ $w }) => $w}; height: ${({ $h }) => $h}px; display: flex; flex-direction: column; min-width: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; padding: 8px 10px; gap: 6px;
`
const Head = styled.div`display: flex; align-items: center; gap: 6px; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; font-family: ${fonts.sans};
  & .spacer { flex: 1; } & .mini { border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 2px; &:hover { color: ${colors.text.primary}; } }`
const Body = styled.div`flex: 1; min-height: 0; display: flex; flex-direction: column;`
const Bar = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap;`
const Empty = styled.div`grid-column: 1 / -1; padding: 40px; text-align: center; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; border: 1px dashed ${colors.border}; border-radius: ${radius.md};`

export function DashboardsView({ app, model, definition, analyses, editing, lang, onChange, onOpenAnalysis }: {
  app: string
  model: string
  definition: DashboardDefinition
  analyses: SavedAnalysis[]
  editing: boolean
  lang: string
  onChange: (next: DashboardDefinition) => void
  onOpenAnalysis: (analysis: SavedAnalysis, drill?: DrillCell[]) => void
}) {
  const { t } = useTranslation()
  const byId = useMemo(() => new Map(analyses.map((a) => [a.id, a])), [analyses])
  const [adding, setAdding] = useState('')
  const period = definition.period ?? null
  const setTile = (i: number, patch: Partial<DashboardTile>) => onChange({ ...definition, tiles: definition.tiles.map((x, k) => (k === i ? { ...x, ...patch } : x)) })
  const move = (i: number, d: -1 | 1) => {
    const j = i + d
    if (j < 0 || j >= definition.tiles.length) return
    const tiles = definition.tiles.slice(); const [x] = tiles.splice(i, 1); tiles.splice(j, 0, x)
    onChange({ ...definition, tiles })
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1, minHeight: 0, overflow: 'auto' }}>
      <Bar>
        <CalendarRange size={13} style={{ color: colors.text.muted }} />
        <DateRangeFilter preset={period?.preset ?? 'all'} range={period?.range ?? { dateFrom: '', dateTo: '' }}
          onChange={(p: DatePreset, r: DateRange) => onChange({ ...definition, period: p === 'all' ? null : { preset: p, range: r } })} />
        <span style={{ fontSize: fontSize.micro, color: colors.text.muted }}>{t('explorer.dashboardPeriodNote')}</span>
        <div style={{ flex: 1 }} />
        {editing && (
          <>
            <div style={{ width: 260 }}>
              <SearchSelect sort value={adding} options={analyses.map((a) => ({ value: a.id, label: a.name, mono: a.id }))} placeholder={t('explorer.addTile')}
                onChange={(v) => { if (!v) return; onChange({ ...definition, tiles: [...definition.tiles, { analysis: v, w: 2, h: 'm' }] }); setAdding('') }} />
            </div>
          </>
        )}
      </Bar>
      <Grid>
        {definition.tiles.length === 0 && <Empty>{t('explorer.noTiles')}</Empty>}
        {definition.tiles.map((tile, i) => {
          const a = byId.get(tile.analysis)
          return (
            <Card key={`${tile.analysis}:${i}`} $w={tile.w} $h={TILE_HEIGHT[tile.h]}>
              <Head>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tile.title || a?.name || tile.analysis}</span>
                <span className="spacer" />
                {editing ? (
                  <>
                    <button type="button" className="mini" onClick={() => move(i, -1)} title={t('explorer.moveLeft')}><ChevronLeft size={13} /></button>
                    <button type="button" className="mini" onClick={() => move(i, 1)} title={t('explorer.moveRight')}><ChevronRight size={13} /></button>
                    <button type="button" className="mini" onClick={() => setTile(i, { w: (tile.w < 4 ? tile.w + 1 : 1) as 1 | 2 | 3 | 4 })} title={t('explorer.width')}><Maximize2 size={13} /> {tile.w}</button>
                    <button type="button" className="mini" onClick={() => setTile(i, { h: ({ s: 'm', m: 'l', l: 's' } as Record<TileHeight, TileHeight>)[tile.h] })} title={t('explorer.height')}><Minimize2 size={13} /> {tile.h}</button>
                    <button type="button" className="mini" onClick={() => onChange({ ...definition, tiles: definition.tiles.filter((_, k) => k !== i) })} title={t('common.delete')} style={{ color: colors.red.main }}><Trash2 size={13} /></button>
                  </>
                ) : a ? (
                  <button type="button" className="mini" onClick={() => onOpenAnalysis(a)} title={t('explorer.openInEditor')}><Pencil size={13} /></button>
                ) : null}
              </Head>
              <Body>
                {a ? <Tile app={app} model={model} analysis={a} period={period} lang={lang} onDrill={(cells) => onOpenAnalysis(a, cells)} />
                  : <Banner $tone="warning">{t('explorer.missingAnalysis', { id: tile.analysis })}</Banner>}
              </Body>
            </Card>
          )
        })}
      </Grid>
    </div>
  )
}

function Tile({ app, model, analysis, period, lang, onDrill }: {
  app: string; model: string; analysis: SavedAnalysis; period: DashboardDefinition['period']; lang: string; onDrill: (cells: DrillCell[]) => void
}) {
  const { t } = useTranslation()
  const def: AnalysisDefinition = useMemo(() => withDashboardPeriod(analysis.definition, period), [analysis, period])
  const [result, setResult] = useState<AggregateResult | null>(null)
  const [detail, setDetail] = useState<DetailResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const key = JSON.stringify(def)
  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    const go = async () => {
      try {
        if (def.view === 'detail') setDetail(await api.post<DetailResult>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(model)}/detail`, buildDetailRequest(def, { pageSize: 50 })))
        else setResult(await api.post<AggregateResult>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(model)}/query`, buildAggregateRequest(def)))
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e))
      } finally { if (!cancelled) setLoading(false) }
    }
    void go()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key is the serialised definition
  }, [app, model, key])
  if (error) return <Banner $tone="error">{error}</Banner>
  if (loading && !result && !detail) return <div style={{ padding: 12 }}><SpinnerRing size={14} thickness={2} /></div>
  if (def.view === 'detail') return detail ? <ResultTable columns={detail.columns} rows={detail.rows} lang={lang} /> : null
  if (!result) return <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('explorer.emptyHint')}</span>
  if (def.view === 'pivot') return <PivotView result={result} measureKey={def.pivot?.measure ?? null} pivotKey={def.pivotColumn ?? null} percent={def.pivot?.percent ?? 'none'} lang={lang} onDrill={(v) => onDrill(drillCellsOf(v, def.rows))} />
  if (def.view === 'table') return <ResultTable columns={result.columns} rows={result.rows} lang={lang} onRowClick={def.rows.length > 0 ? (r) => onDrill(drillCellsOf(r, def.rows)) : undefined} />
  return <ChartView result={result} view={def.view as 'bar' | 'line' | 'pie' | 'kpi'} measureKey={def.chart?.measure ?? null} seriesKey={def.pivotColumn ?? null} stacked={!!def.chart?.stacked} lang={lang} connector={app} />
}
