import { describe, expect, it } from 'vitest'
import { apiGroups, CONFIG_LABELS, docBlocks, filterKeys, statusCounts, filterTables, tableGroups, filterRoutes, keyLabel, keyStatus, readerModules, sectionCounts } from './devref'
import type { ApiRoute, ConfigKey, DataTableRef } from '../types/devref'

const k = (key: string, over: Partial<ConfigKey> = {}): ConfigKey => ({
  key, section: key.split('.')[0], name: key.split('.')[1], type: 'str', default: null, description: null,
  editor: null, file_only: null, readers: [], ...over,
})

describe('keyStatus', () => {
  it('editor, file-only, gap', () => {
    expect(keyStatus(k('app.name', { editor: 'Settings → App' }))).toBe('editor')
    expect(keyStatus(k('crypto.master_key', { file_only: 'env' }))).toBe('file')
    expect(keyStatus(k('app.base_path'))).toBe('gap')
  })
})

describe('filterKeys', () => {
  const keys = [
    k('app.name', { editor: 'x' }), k('app.base_path', { description: 'The URL prefix' }),
    k('jobs.files_dir', { readers: [{ file: 'liberty/jobs/files.py', line: 3, snippet: '', via: null }] }),
  ]
  it('by section, gaps, text in key / description / reader file', () => {
    expect(filterKeys(keys, '', 'app', '').map((x) => x.key)).toEqual(['app.name', 'app.base_path'])
    expect(filterKeys(keys, '', '', 'gap').map((x) => x.key)).toEqual(['app.base_path', 'jobs.files_dir'])
    expect(filterKeys(keys, 'prefix', '', '').map((x) => x.key)).toEqual(['app.base_path'])
    expect(filterKeys(keys, 'jobs/files', '', '').map((x) => x.key)).toEqual(['jobs.files_dir'])
  })
})

describe('readerModules / sectionCounts', () => {
  it('modules from the reader files, once each', () => {
    const key = k('jobs.files_dir', { readers: [
      { file: 'liberty/jobs/files.py', line: 1, snippet: '', via: null },
      { file: 'liberty/jobs/runner.py', line: 2, snippet: '', via: null },
      { file: 'liberty/main.py', line: 3, snippet: '', via: null },
    ] })
    expect(readerModules(key)).toEqual(['jobs', 'main'])
  })
  it('keys and gaps per section', () => {
    expect(sectionCounts([k('app.a', { editor: 'x' }), k('app.b'), k('jobs.c')])).toEqual([
      { section: 'app', total: 2, gaps: 1 }, { section: 'jobs', total: 1, gaps: 1 }])
  })
})

describe('apiGroups / filterRoutes', () => {
  const r = (path: string, group: string, category: string, over: Partial<ApiRoute> = {}): ApiRoute => ({
    method: 'GET', path, summary: '', description: '', group, category, tag: '', access: 'public', permissions: [],
    file: '', line: 0, in_schema: true, params: [], parameters: [], request: null, response: null, ...over,
  })
  const routes = [r('/a', 'jobs', 'nomaflow'), r('/b', 'auth', 'Access'), r('/c', 'jobs', 'nomaflow', { permissions: ['sql:{c}:{q}'] })]
  it('categories in order, groups sorted with counts', () => {
    expect(apiGroups(routes)).toEqual([
      { category: 'nomaflow', groups: [{ group: 'jobs', n: 2 }] },
      { category: 'Access', groups: [{ group: 'auth', n: 1 }] },
    ])
  })
  it('by group and by text in path / permission', () => {
    expect(filterRoutes(routes, '', 'jobs').map((x) => x.path)).toEqual(['/a', '/c'])
    expect(filterRoutes(routes, 'sql:', '').map((x) => x.path)).toEqual(['/c'])
  })
})

describe('CONFIG_LABELS', () => {
  it('every label key exists in both locales', async () => {
    const { default: en } = await import('../locales/en')
    const { default: fr } = await import('../locales/fr')
    const get = (o: unknown, path: string) => path.split('.').reduce<unknown>((n, p) => (n && typeof n === 'object' ? (n as Record<string, unknown>)[p] : undefined), o)
    const missing = Object.values(CONFIG_LABELS).filter((k) => typeof get(en, k) !== 'string' || typeof get(fr, k) !== 'string')
    expect(missing).toEqual([])
  })
  it('falls back to the file name, then the description', () => {
    const t = (k: string, o?: Record<string, unknown>) => (o ? `${k}:${o.section}` : k)
    expect(keyLabel(k('screens.config_path'), t)).toBe('developer.labels.fileOf:screens')
    expect(keyLabel(k('x.y', { description: 'First. Second.' }), t)).toBe('First')
    expect(keyLabel(k('app.name'), t)).toBe('settings.app.name')
  })
})

describe('docBlocks', () => {
  it('joins wrapped lines, marks code and emphasis, keeps lists', () => {
    const doc = 'Validate a batch for *connector*.\n\nEach row runs ``insert_query``\nwith the same machinery.\n\n- first item\n  continued\n- second'
    expect(docBlocks(doc)).toEqual([
      { kind: 'p', parts: [{ text: 'Validate a batch for ' }, { text: 'connector', em: true }, { text: '.' }] },
      { kind: 'p', parts: [{ text: 'Each row runs ' }, { text: 'insert_query', code: true }, { text: ' with the same machinery.' }] },
      { kind: 'list', items: [[{ text: 'first item continued' }], [{ text: 'second' }]] },
    ])
  })
})

describe('filterTables / tableGroups', () => {
  const tb = (id: string, owner: string, group: string, over: Partial<DataTableRef> = {}): DataTableRef => ({
    id, name: id.split(':')[1], owner, group, label: '', description: '', physical: '', physical_raw: '', key: [], related: [],
    slots: [], used_by: [], columns: [], ...over,
  })
  const tables = [tb('jde:f0092', 'connector', 'jde', { columns: [{ name: 'ULUSER', type: '', label: 'User', key: true, nullable: true, references: null, screens: [], description: '' }] }),
    tb('framework:runs', 'framework', 'nomaflow'), tb('jde:f0005', 'connector', 'jde')]
  it('framework first, then connectors, tables by name', () => {
    expect(tableGroups(tables).map((g) => [g.group, g.tables.map((t) => t.name)])).toEqual([['nomaflow', ['runs']], ['jde', ['f0005', 'f0092']]])
  })
  it('matches a column name or label', () => {
    expect(filterTables(tables, 'user').map((t) => t.id)).toEqual(['jde:f0092'])
  })
})

describe('statusCounts', () => {
  it('counts each status apart (file-only is not editable)', () => {
    expect(statusCounts([k('a.b', { editor: 'x' }), k('a.c', { file_only: 'env' }), k('a.d')])).toEqual({ editor: 1, file: 1, gap: 1 })
  })
})
