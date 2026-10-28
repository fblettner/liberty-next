import { describe, expect, it } from 'vitest'
import { filterKeys, keyStatus, readerModules, sectionCounts } from './devref'
import type { ConfigKey } from '../types/devref'

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
    expect(filterKeys(keys, '', 'app', false).map((x) => x.key)).toEqual(['app.name', 'app.base_path'])
    expect(filterKeys(keys, '', '', true).map((x) => x.key)).toEqual(['app.base_path', 'jobs.files_dir'])
    expect(filterKeys(keys, 'prefix', '', false).map((x) => x.key)).toEqual(['app.base_path'])
    expect(filterKeys(keys, 'jobs/files', '', false).map((x) => x.key)).toEqual(['jobs.files_dir'])
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
