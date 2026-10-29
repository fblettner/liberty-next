// nomajde → User Management (F0092) and Security Workbench (F00950), on a test user only:
// add the user through the form (the save runs the reset_password AIS call → F98OWSEC), give it an
// application security record, delete that record, delete the user (its on_delete action cleans
// every table). The run stops before touching anything if LYTEST01 already exists somewhere.
import { test, expect, type Page } from '@playwright/test'
import * as XLSX from 'xlsx'
import {
  cleanupWrite, confirm, dialog, fill, gridRow, matchesBaseline, normalize, openScreen, pick,
  runGrid, saveBulkEdit, saveDialog, select, startBulkEdit,
} from './support'

const USER = 'LYTEST01'
const OBJECT = 'P0092'

// Every table the user lives in — the ones the delete_user action cleans, plus F0092 / F00921.
const TABLES: { name: string; from: string; where: string }[] = [
  { name: 'F0092', from: '#SCHEMA.SY#.F0092#DBLINK.SY#', where: 'TRIM(ULUSER) = :U' },
  { name: 'F00921', from: '#SCHEMA.SY#.F00921#DBLINK.SY#', where: 'TRIM(ULUSER) = :U' },
  { name: 'F98OWSEC', from: '#SCHEMA.SY#.F98OWSEC#DBLINK.SY#', where: 'TRIM(SCUSER) = :U' },
  { name: 'F0093', from: '#SCHEMA.SY#.F0093#DBLINK.SY#', where: 'TRIM(LLUSER) = :U' },
  { name: 'F95921', from: '#SCHEMA.SY#.F95921#DBLINK.SY#', where: 'TRIM(RLTOROLE) = :U OR TRIM(RLFRROLE) = :U' },
  { name: 'F00950', from: '#SCHEMA.SY#.F00950#DBLINK.SY#', where: 'TRIM(FSUSER) = :U' },
  { name: 'F00950W', from: '#SCHEMA.SY#.F00950W#DBLINK.SY#', where: 'TRIM(WSWOUSER) = :U' },
  { name: 'F9006', from: '#SCHEMA.CTL#.F9006#DBLINK.CTL#', where: 'TRIM(TDVARNAME) = :U' },
]
// Never compared: the passwords, and what changes on every run (date / time / machine updated).
const SECRET = ['scowpwd', 'scsecpwd', 'scsrvbloba']
const VOLATILE = /(upmj|upmt|jobn|seclst)$/i

// F98OWSEC holds a BLOB (scsrvbloba) a thin connection can't read over a dblink: its columns are
// listed, without the BLOB and the passwords.
const COLUMNS: Record<string, string> = {
  F98OWSEC: 'SCUSER, SCSECFRQ, SCSECUSR, SCSECTPE, SCUGRP, SCSECF1, SCSECF2, SCSECF3, SCSECV1, SCSECV2, SCSECV3, '
    + 'SCSECV4, SCATTEMPTS, SCEUSER, SCRETRY, SCMUSE, SCPID',
}

async function snapshot(): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {}
  for (const t of TABLES) {
    const rows = await select('jdedwards', `SELECT ${COLUMNS[t.name] ?? '*'} FROM ${t.from} WHERE ${t.where}`, { U: USER })
    const drop = [...SECRET, ...Object.keys(rows[0] ?? {}).filter((k) => VOLATILE.test(k))]
    out[t.name] = normalize(rows, drop)
  }
  return out
}

const empty = Object.fromEntries(TABLES.map((t) => [t.name, []]))

async function cleanup() {
  for (const t of [...TABLES].reverse()) {
    await cleanupWrite('jdedwards', `DELETE FROM ${t.from} WHERE ${t.where}`, { U: USER })
  }
}

/** An .xlsx file with *rows* (headers = the column names), written to the test's output folder. */
function sheet(name: string, rows: Record<string, unknown>[]): string {
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows), 'Sheet1')
  const file = test.info().outputPath(`${name}.xlsx`)
  XLSX.writeFile(wb, file)
  return file
}

async function openRow(page: Page, text: string) {
  await gridRow(page, text).first().click()
  await expect(dialog(page)).toBeVisible()
}

async function deleteOpenRecord(page: Page) {
  await dialog(page).getByRole('button', { name: 'Delete', exact: true }).click()
  await confirm(page, 'Delete')
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0, { timeout: 15_000 })
}

test('F0092 / F00950: add a user, set and remove its security, delete the user', async ({ context }) => {
  expect(await snapshot(), `${USER} already exists somewhere — clean it first`).toEqual(empty)
  let failed = true
  try {
    await test.step('F0092: add the user (on_insert → reset_password through AIS)', async () => {
      const page = await openScreen(context, 'nomajde', 'f0092')
      await page.getByTitle('Open the form to add a new row').click()
      await fill(dialog(page), 'User ID', USER)
      await saveDialog(page)
      matchesBaseline('jde-1-user-add', await snapshot())
      await page.close()
    })

    await test.step('F00950: give the user application security on P0092', async () => {
      const page = await openScreen(context, 'nomajde', 'f00950')
      await page.locator('input[placeholder^="filter"]').first().fill(OBJECT)
      await runGrid(page)
      await page.getByTitle('Open the form to add a new row').click()
      const d = dialog(page)
      await pick(page, d, 'Security Group', 'OBJECTS')
      await pick(page, d, 'Security Type', '3')
      await pick(page, d, 'User / Role', USER)
      await pick(page, d, 'Object Name', OBJECT)
      await saveDialog(page)
      matchesBaseline('jde-2-security-add', await snapshot())

      await runGrid(page)
      await openRow(page, USER)
      await deleteOpenRecord(page)
      matchesBaseline('jde-3-security-delete', await snapshot())
      await page.close()
    })

    await test.step('F00950: import a record, then the same record changed (bulk edit quick import)', async () => {
      const page = await openScreen(context, 'nomajde', 'f00950')
      await page.locator('input[placeholder^="filter"]').first().fill(OBJECT)
      // A new record without its product code (FSSY): the object's lookup return-fill supplies it.
      await startBulkEdit(page)
      await page.locator('input[type="file"]').first().setInputFiles(sheet('f00950-new', [{ FSSETY: '3', FSUSER: USER, FSOBNM: OBJECT, FSRUN: 'N' }]))
      await expect(page.locator('tbody tr').filter({ has: page.locator('[title="new row"]') })).toHaveCount(1)
      await saveBulkEdit(page, USER)
      matchesBaseline('jde-4-import-new', await snapshot())

      // The same key with Run = Y: matched on the key, an edit of the existing row (not a new one).
      await startBulkEdit(page)
      await page.locator('input[type="file"]').first().setInputFiles(sheet('f00950-change', [{ FSSETY: '3', FSUSER: USER, FSOBNM: OBJECT, FSRUN: 'Y' }]))
      await expect(page.locator('tbody tr').filter({ has: page.locator('[title="edited"]') })).toHaveCount(1)
      await saveBulkEdit(page, USER)
      matchesBaseline('jde-5-import-change', await snapshot())
      await page.close()
    })

    await test.step('F0092: the Import dialog checks two rows (one breaks a screen rule), then Cancel — nothing saved', async () => {
      const others = async () => (await select('jdedwards',
        `SELECT COUNT(*) N FROM #SCHEMA.SY#.F0092#DBLINK.SY# WHERE TRIM(ULUSER) IN ('LYTEST02', 'LYTEST03')`))[0].n
      expect(await others(), 'LYTEST02 / LYTEST03 already exist').toBe(0)
      const page = await openScreen(context, 'nomajde', 'f0092')
      await page.getByRole('button', { name: 'Import', exact: true }).click()
      await page.locator('input[type="file"]').last().setInputFiles(
        sheet('f0092-check', [{ ULUSER: 'LYTEST02' }, { ULUSER: 'LYTEST03', ULLNGP: 'ZZ' }]))
      await page.getByRole('button', { name: 'Check', exact: true }).click()
      await expect(page.getByText('✓ 1 valid')).toBeVisible({ timeout: 10_000 })
      await expect(page.getByText('✕ 1 invalid')).toBeVisible()
      await expect(page.getByText('“ZZ” is not a valid value')).toBeVisible()
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
      expect(await others()).toBe(0)
      await page.close()
    })

    await test.step('F0092: a form action with a prompt — cancelled, then confirmed (Reset Password)', async () => {
      const page = await openScreen(context, 'nomajde', 'f0092')
      await openRow(page, USER)
      const prompt = page.getByText('No inputs required — click Confirm to continue.')
      // Cancel: the prompt closes, nothing runs, the form stays open.
      await page.getByRole('button', { name: 'Reset Password', exact: true }).click()
      await expect(prompt).toBeVisible()
      await page.getByRole('button', { name: 'Cancel', exact: true }).last().click()
      await expect(prompt).toHaveCount(0)
      await expect(dialog(page)).toBeVisible()
      // (Known, docs/work/PLAN.md findings: the form still reports "Reset Password · OK" after a
      // cancelled prompt — the tab-action handler ignores the runner's ``cancelled``.)
      // Confirm: the chain runs (reset_password through AIS, on the test user) and reports OK.
      await page.getByRole('button', { name: 'Reset Password', exact: true }).click()
      await page.getByRole('button', { name: 'Confirm', exact: true }).click()
      await expect(page.getByText('Reset Password · OK')).toBeVisible({ timeout: 15_000 })
      // The form's Cancel (the status banner's ✕ is also labelled "Cancel").
      await page.locator('button', { hasText: /^\s*Cancel\s*$/ }).click()
      await page.close()
    })

    await test.step('F0092: delete the user (on_delete → delete_user in every table)', async () => {
      const page = await openScreen(context, 'nomajde', 'f0092')
      await openRow(page, USER)
      await deleteOpenRecord(page)
      expect(await snapshot()).toEqual(empty)
    })
    failed = false
  } finally {
    if (failed) await cleanup()
  }
})
