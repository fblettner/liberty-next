import { describe, expect, it } from 'vitest'
import { SCREEN_STAGES, columnMarks, moved, screenFigures, selectionAfterMove, selectionAfterRemove, stageOf } from './screenStages'

describe('a screen as stages', () => {
  it('puts every section of the designer in one stage, in building order', () => {
    expect(SCREEN_STAGES.map((s) => s.id)).toEqual(['general', 'data', 'grid', 'form', 'actions', 'output'])
    expect(SCREEN_STAGES.flatMap((s) => s.tabs).sort()).toEqual(['actions', 'columns', 'dialog', 'export', 'general', 'queries', 'rowmenu', 'summary', 'views'])
    expect(stageOf('views').id).toBe('grid')
    expect(stageOf('rowmenu').id).toBe('actions')
  })
  it('counts what a screen holds', () => {
    const f = screenFigures({
      label: 'Codes', read_query: 'codes_get', update_query: 'code_put', delete_query: '', save_connector: 'ais',
      columns: [{ name: 'KY', filter: true }, { name: 'DL01' }], views: [{ name: 'Short' }], summary: { dimensions: [] },
      dialog: { tabs: [{ id: 'g', fields: [{ name: 'KY' }, { name: 'DL01' }] }, { id: 'n', type: 'nested_table' }], on_save: [{ id: 'a' }] },
      actions: [{ id: 'x' }], row_menu: [{ id: 'y' }, { id: 'z' }], on_insert: [{ id: 'h' }], export: { sheets: [{ name: 's' }] },
    }, 'codes')
    expect(f).toEqual({ label: 'Codes', readOnly: false, read: 'codes_get', saves: 1, saveConnector: 'ais', columns: 2, filters: 1, views: 1, summary: true,
      hasForm: true, tabs: 2, fields: 2, toolbar: 1, rowMenu: 2, hooks: 2, sheets: 1 })
    const bare = screenFigures({ read_query: 'q', read_only: true }, 'bare')
    expect(bare.label).toBe('bare')
    expect(bare.readOnly && !bare.hasForm && bare.saves === 0 && bare.columns === 0).toBe(true)
  })
})

describe('the columns list', () => {
  it('says what a column is without opening it', () => {
    expect(columnMarks({ name: 'CSI_ID', key: true, filter: true, rules: 'lookup', rules_values: 'csi' })).toEqual({ marks: ['key', 'filter'], rule: 'LOOKUP csi' })
    expect(columnMarks({ name: 'X', hidden: true, required: true, disabled: true, sensitive: true })).toEqual({ marks: ['hidden', 'required', 'readonly', 'sensitive'], rule: '' })
    expect(columnMarks({ name: 'PLAIN' })).toEqual({ marks: [], rule: '' })
  })
  it('moves a column and keeps the selection on the same one', () => {
    expect(moved(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd'])
    expect(moved(['a', 'b'], 1, 1)).toEqual(['a', 'b'])
    expect(moved(['a', 'b'], 0, 5)).toEqual(['a', 'b'])
    expect(selectionAfterMove(0, 0, 2)).toBe(2)      // the moved one is the selected one
    expect(selectionAfterMove(1, 0, 2)).toBe(0)      // one from above went below it
    expect(selectionAfterMove(1, 3, 0)).toBe(2)      // one from below went above it
    expect(selectionAfterMove(3, 0, 1)).toBe(3)      // a move that does not cross it
    expect(selectionAfterMove(null, 0, 1)).toBeNull()
  })
  it('keeps a selection after a removal', () => {
    expect(selectionAfterRemove(2, 0, 3)).toBe(1)    // one above was removed
    expect(selectionAfterRemove(1, 1, 3)).toBe(1)    // the selected one: the next takes its place
    expect(selectionAfterRemove(3, 3, 3)).toBe(2)    // the last one: the one before
    expect(selectionAfterRemove(0, 2, 3)).toBe(0)
    expect(selectionAfterRemove(0, 0, 0)).toBeNull()
  })
})
