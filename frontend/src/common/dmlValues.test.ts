// parseDmlValues turns a stored DML redo (the audit trail's statement text) into per-column
// BEFORE / AFTER rows — the "in-flight value diff" panel. Column order is first-seen; values
// are unquoted; SQL-escaped quotes ('') are unescaped.
import { describe, expect, it } from 'vitest'
import { parseDmlValues } from './dmlValues'

describe('parseDmlValues', () => {
  it('INSERT: positional column / value pairs land as AFTER', () => {
    const rows = parseDmlValues(`INSERT INTO "SY"."F0092"("ULUSER", "ULAN8", "ULUGRP") VALUES ('JDE', '1001', 'ADMIN')`)
    expect(rows).toEqual([
      { name: 'ULUSER', before: null, after: 'JDE' },
      { name: 'ULAN8', before: null, after: '1001' },
      { name: 'ULUGRP', before: null, after: 'ADMIN' },
    ])
  })

  it('UPDATE: SET pairs are AFTER, top-level WHERE pairs are BEFORE, in first-seen order', () => {
    const rows = parseDmlValues(`UPDATE "SY"."F0092" SET "ULUGRP" = 'MANAGER', "ULDS" = 'O''Brien' WHERE "ULUSER" = 'JDE' AND "ULUGRP" = 'ADMIN'`)
    expect(rows).toEqual([
      { name: 'ULUGRP', before: 'ADMIN', after: 'MANAGER' },
      { name: 'ULDS', before: null, after: "O'Brien" },
      { name: 'ULUSER', before: 'JDE', after: null },
    ])
  })

  it('DELETE: every WHERE pair is a BEFORE value', () => {
    const rows = parseDmlValues(`DELETE FROM "SY"."F0092" WHERE "ULUSER" = 'JDE' AND "ULAN8" = '1001'`)
    expect(rows).toEqual([
      { name: 'ULUSER', before: 'JDE', after: null },
      { name: 'ULAN8', before: '1001', after: null },
    ])
  })

  it('the operation can be forced, and a WHERE inside a string literal is not a clause boundary', () => {
    const rows = parseDmlValues(`UPDATE t SET "NOTE" = 'see WHERE clause' WHERE "ID" = '7'`, 'update')
    expect(rows).toEqual([
      { name: 'NOTE', before: null, after: 'see WHERE clause' },
      { name: 'ID', before: '7', after: null },
    ])
  })

  it('empty / unparseable input is an empty list, never a throw', () => {
    expect(parseDmlValues('')).toEqual([])
    expect(parseDmlValues('INSERT INTO t VALUES')).toEqual([])
    expect(parseDmlValues('SELECT 1')).toEqual([])
  })
})
