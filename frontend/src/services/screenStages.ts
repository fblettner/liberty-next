// A screen seen as the stages it is made of — what it is → its data → its grid → its record form → its actions →
// what it gives out — and what each stage holds, in a line, from the screen's own configuration. No React here.

export type ScreenTab = 'general' | 'queries' | 'columns' | 'views' | 'summary' | 'dialog' | 'actions' | 'rowmenu' | 'export'
export type ScreenStageId = 'general' | 'data' | 'grid' | 'form' | 'actions' | 'output'
export interface ScreenStage { id: ScreenStageId; tabs: ScreenTab[] }

/** The stages, in the order a screen is built — and the designer's sections each one holds. */
export const SCREEN_STAGES: ScreenStage[] = [
  { id: 'general', tabs: ['general'] },
  { id: 'data', tabs: ['queries'] },
  { id: 'grid', tabs: ['columns', 'views', 'summary'] },
  { id: 'form', tabs: ['dialog'] },
  { id: 'actions', tabs: ['actions', 'rowmenu'] },
  { id: 'output', tabs: ['export'] },
]

export const stageOf = (tab: ScreenTab): ScreenStage => SCREEN_STAGES.find((s) => s.tabs.includes(tab)) ?? SCREEN_STAGES[0]

type Row = Record<string, unknown>
const list = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : [])
const text = (v: unknown): string => (typeof v === 'string' ? v : '')

/** The figures a stage's line is written from (the page words them in the user's language). */
export interface ScreenFigures {
  label: string; readOnly: boolean
  read: string; saves: number; saveConnector: string
  columns: number; filters: number; views: number; summary: boolean
  hasForm: boolean; tabs: number; fields: number
  toolbar: number; rowMenu: number; hooks: number
  sheets: number
}

export function screenFigures(screen: Row, id: string): ScreenFigures {
  const dialog = (screen.dialog ?? null) as Row | null
  const tabs = list(dialog?.tabs)
  const hooks = ['on_insert', 'on_update', 'on_delete', 'on_duplicate'].reduce((n, k) => n + list(screen[k]).length, 0)
    + ['on_load', 'on_save', 'on_cancel'].reduce((n, k) => n + list(dialog?.[k]).length, 0)
  const exportSpec = (screen.export ?? null) as Row | null
  return {
    label: text(screen.label) || id, readOnly: screen.read_only === true,
    read: text(screen.read_query), saves: ['insert_query', 'update_query', 'delete_query'].filter((k) => text(screen[k])).length,
    saveConnector: text(screen.save_connector),
    columns: list(screen.columns).length, filters: list(screen.columns).filter((c) => c.filter === true).length,
    views: list(screen.views).length, summary: screen.summary != null,
    hasForm: dialog != null, tabs: tabs.length, fields: tabs.reduce((n, tab) => n + list(tab.fields).length, 0),
    toolbar: list(screen.actions).length, rowMenu: list(screen.row_menu).length, hooks,
    sheets: list(exportSpec?.sheets).length,
  }
}

/** What a column's line says beside its name: the marks worth seeing without opening it. */
export type ColumnMark = 'key' | 'filter' | 'hidden' | 'required' | 'readonly' | 'sensitive'
export function columnMarks(col: Row): { marks: ColumnMark[]; rule: string } {
  const marks: ColumnMark[] = []
  if (col.key === true) marks.push('key')
  if (col.filter === true) marks.push('filter')
  if (col.hidden === true) marks.push('hidden')
  if (col.required === true) marks.push('required')
  if (col.disabled === true) marks.push('readonly')
  if (col.sensitive === true) marks.push('sensitive')
  const values = text(col.rules_values)
  return { marks, rule: text(col.rules) ? text(col.rules).toUpperCase() + (values ? ` ${values}` : '') : '' }
}

/** The list with the item at `from` moved to `to` (the columns' stored order is the grid's). */
export function moved<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items
  const next = items.slice()
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

/** Where the selection lands after a move or a removal, so the panel beside keeps showing the same column. */
export function selectionAfterMove(selected: number | null, from: number, to: number): number | null {
  if (selected == null || from === to) return selected
  if (selected === from) return to
  if (from < selected && to >= selected) return selected - 1
  if (from > selected && to <= selected) return selected + 1
  return selected
}
export function selectionAfterRemove(selected: number | null, removed: number, remaining: number): number | null {
  if (selected == null || remaining === 0) return null
  if (selected === removed) return Math.min(removed, remaining - 1)
  return selected > removed ? selected - 1 : selected
}
