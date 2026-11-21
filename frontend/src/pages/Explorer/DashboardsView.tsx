// Dashboards of analyses: a 4-column grid of tiles, each running one saved analysis in its own
// view (table / pivot / chart / KPI / detail), with the dashboard's period overriding the
// analyses that have a period field. Edit-layout mode moves a tile by drag and drop (its grip), sizes it by
// its corner, removes and adds tiles — the same gestures as the dashboards' editor (services/dashLayout.ts).
// A tile's pencil opens its analysis in the editor; a drill from a tile does the same with the
// cell's values as filters.
import { useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { GripVertical, Pencil, Trash2, CalendarRange } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, DateRangeFilter, SearchSelect, SpinnerRing, type DatePreset, type DateRange } from '../../common'
import { buildAggregateRequest, buildDetailRequest, drillCellsOf, showModes } from '../../services/explorer'
import { dropSide, dropped, nearestSize, resized, stepped, steppedSize } from '../../services/dashLayout'
import { TILE_HEIGHT, withDashboardPeriod, type DashboardDefinition, type DashboardTile, type SavedAnalysis, type TileHeight } from '../../services/explorerSaved'
import type { AggregateResult, AnalysisDefinition, DetailResult, DrillCell } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ShelfBox, ShelfTitle } from './shelfKit'
import { ResultTable } from './ResultTable'
import { PivotView } from './PivotView'
import { ChartView } from './ChartView'

const GAP_PX = 12
const COLUMNS = 4
const Grid = styled.div`display: grid; grid-template-columns: repeat(${COLUMNS}, 1fr); gap: ${GAP_PX}px; align-content: start; @media (max-width: 1100px) { grid-template-columns: repeat(2, 1fr); }`
const Card = styled.div<{ $w: number; $h: number }>`
  position: relative; grid-column: span ${({ $w }) => $w}; height: ${({ $h }) => $h}px; display: flex; flex-direction: column; min-width: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; padding: 8px 10px; gap: 6px;
  &[data-editing='true']:hover, &[data-sizing='true'] { border-color: ${colors.blue.border}; }
  &[data-editing='true']:hover .corner, &[data-sizing='true'] .corner { opacity: 1; }
  &[data-dragged='true'] { opacity: 0.45; }
  /* where a dragged tile would land: a bar on that side */
  &[data-drop]::before { content: ''; position: absolute; top: 0; bottom: 0; width: 4px; border-radius: 2px; background: ${colors.blue.main}; z-index: 6; }
  &[data-drop='before']::before { left: -9px; }
  &[data-drop='after']::before { right: -9px; }
`
const Head = styled.div`display: flex; align-items: center; gap: 6px; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; font-family: ${fonts.sans};
  & .spacer { flex: 1; } & .mini { border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 2px; &:hover { color: ${colors.text.primary}; } }
  & .grip { cursor: grab; } & .grip:active { cursor: grabbing; }
  & .size { font-size: ${fontSize.micro}; font-family: ${fonts.mono}; font-weight: 400; color: ${colors.text.muted}; }`
// the corner a tile is sized by
const Corner = styled.div`
  position: absolute; right: -2px; bottom: -2px; width: 18px; height: 18px; z-index: 5; cursor: nwse-resize; opacity: 0; transition: opacity 0.12s; touch-action: none;
  border-right: 3px solid ${colors.blue.main}; border-bottom: 3px solid ${colors.blue.main}; border-radius: 0 0 ${radius.md} 0;
`
type TileWidth = DashboardTile['w']
const Body = styled.div`flex: 1; min-height: 0; display: flex; flex-direction: column;`
const Empty = styled.div`grid-column: 1 / -1; padding: 40px; text-align: center; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: var(--bg-modal);`

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
  // ── moving and sizing the tiles (edit layout) ────────────────────────────────────────────────
  const gridRef = useRef<HTMLDivElement>(null)
  const dragFrom = useRef<number | null>(null)
  const [dragged, setDragged] = useState<number | null>(null)
  const [dropAt, setDropAt] = useState<{ over: number; side: 'before' | 'after' } | null>(null)
  const [sizing, setSizing] = useState<{ i: number; w: TileWidth; h: TileHeight } | null>(null)
  const endDrag = () => { dragFrom.current = null; setDragged(null); setDropAt(null) }
  const sideAt = (i: number, e: React.DragEvent<HTMLDivElement>) => {
    const from = dragFrom.current
    if (from == null) return null
    e.preventDefault()
    const box = e.currentTarget.getBoundingClientRect()
    return { from, side: dropSide(from, i, e.clientX, box.left, box.width) }
  }
  const onDragOverTile = (i: number, e: React.DragEvent<HTMLDivElement>) => {
    const at = sideAt(i, e)
    if (!at) return
    const side = at.side
    setDropAt((cur) => (side == null ? null : cur && cur.over === i && cur.side === side ? cur : { over: i, side }))
  }
  const onDropTile = (i: number, e: React.DragEvent<HTMLDivElement>) => {
    const at = sideAt(i, e)
    if (!at) return
    if (at.side) onChange({ ...definition, tiles: dropped(definition.tiles, at.from, i, at.side === 'after') })
    endDrag()
  }
  // the corner: the tile follows the pointer — whole columns, and the closest of the three heights
  const startSizing = (i: number, e: React.PointerEvent<HTMLDivElement>) => {
    const grid = gridRef.current
    if (!grid) return
    e.preventDefault(); e.stopPropagation()
    const tile = definition.tiles[i]
    const columns = getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length || COLUMNS   // 2 on a narrow window
    const colPx = (grid.getBoundingClientRect().width + GAP_PX) / columns
    const x0 = e.clientX, y0 = e.clientY
    let last = { w: tile.w, h: tile.h }
    const onMove = (ev: PointerEvent) => {
      last = { w: resized(tile.w, 1, ev.clientX - x0, 0, colPx, 1, COLUMNS).col_span as TileWidth, h: nearestSize(TILE_HEIGHT, TILE_HEIGHT[tile.h] + ev.clientY - y0) }
      setSizing({ i, ...last })
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp)
      setSizing(null)
      if (last.w !== tile.w || last.h !== tile.h) setTile(i, last)
    }
    window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp)
    setSizing({ i, ...last })
  }
  // the grip takes the keyboard too: arrows move the tile, Shift + arrows size it
  const onGripKey = (i: number, e: React.KeyboardEvent) => {
    const arrow = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key] as [number, number] | undefined
    if (!arrow) return
    e.preventDefault()
    const tile = definition.tiles[i]
    if (e.shiftKey) setTile(i, { w: stepped(tile.w, 1, arrow[0], 0, COLUMNS).col_span as TileWidth, h: steppedSize(TILE_HEIGHT, tile.h, arrow[1]) })
    else if (arrow[0]) move(i, arrow[0] as -1 | 1)
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1, minHeight: 0, overflow: 'auto' }}>
      <ShelfBox $tone="purple" data-shelf="period">
        <ShelfTitle $tone="purple"><span className="chip"><CalendarRange size={13} /></span>{t('explorer.period')}</ShelfTitle>
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
      </ShelfBox>
      <Grid ref={gridRef}>
        {definition.tiles.length === 0 && <Empty>{t('explorer.noTiles')}</Empty>}
        {definition.tiles.map((tile, i) => {
          const a = byId.get(tile.analysis)
          const live = sizing?.i === i ? sizing : null            // its corner is being dragged
          const w = live?.w ?? tile.w, h = live?.h ?? tile.h
          return (
            <Card key={`${tile.analysis}:${i}`} $w={w} $h={TILE_HEIGHT[h]} data-tile={i} data-editing={editing || undefined}
              data-dragged={dragged === i || undefined} data-sizing={live ? 'true' : undefined} data-drop={dropAt?.over === i ? dropAt.side : undefined}
              onDragOver={editing ? (e) => onDragOverTile(i, e) : undefined} onDrop={editing ? (e) => onDropTile(i, e) : undefined}>
              <Head>
                {editing && (
                  <button type="button" className="mini grip" draggable title={t('settings.dash.drag')} aria-label={t('settings.dash.drag')} data-grip={i}
                    onDragStart={(e) => {
                      dragFrom.current = i; setDragged(i)
                      e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(i))
                      const card = e.currentTarget.closest('[data-tile]')
                      if (card) e.dataTransfer.setDragImage(card, 24, 16)
                    }}
                    onDragEnd={endDrag} onKeyDown={(e) => onGripKey(i, e)}>
                    <GripVertical size={13} />
                  </button>
                )}
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tile.title || a?.name || tile.analysis}</span>
                <span className="spacer" />
                {editing ? (
                  <>
                    <span className="size">{w} × {h.toUpperCase()}</span>
                    <button type="button" className="mini" onClick={() => onChange({ ...definition, tiles: definition.tiles.filter((_, k) => k !== i) })} title={t('common.delete')} style={{ color: colors.red.main }}><Trash2 size={13} /></button>
                  </>
                ) : a ? (
                  <button type="button" className="mini" onClick={() => onOpenAnalysis(a)} title={t('explorer.openInEditor')}><Pencil size={13} /></button>
                ) : null}
              </Head>
              {editing && <Corner className="corner" title={t('settings.dash.size')} data-corner={i} onPointerDown={(e) => startSizing(i, e)} />}
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
  const shows = useMemo(() => showModes(def.rows), [def.rows])
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
  if (def.view === 'detail') return detail ? <ResultTable bare shows={shows} columns={detail.columns} rows={detail.rows} lang={lang} /> : null
  if (!result) return <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('explorer.emptyHint')}</span>
  if (def.view === 'pivot') return <PivotView result={result} measureKey={def.pivot?.measure ?? null} pivotKey={def.pivotColumn ?? null} percent={def.pivot?.percent ?? 'none'} lang={lang} shows={shows} onDrill={(v) => onDrill(drillCellsOf(v, def.rows))} />
  if (def.view === 'table') return <ResultTable bare shows={shows} columns={result.columns} rows={result.rows} lang={lang} onRowClick={def.rows.length > 0 ? (r) => onDrill(drillCellsOf(r, def.rows)) : undefined} />
  return <ChartView result={result} view={def.view as 'bar' | 'line' | 'pie' | 'kpi'} measureKey={def.chart?.measure ?? null} seriesKey={def.pivotColumn ?? null} stacked={!!def.chart?.stacked} lang={lang} connector={app} shows={shows} />
}
