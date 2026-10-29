// nomajde → User Management (F0092) and Security Workbench (F00950), on a test user only:
// add the user through the form (the save runs the reset_password AIS call → F98OWSEC), give it an
// application security record, delete that record, delete the user (its on_delete action cleans
// every table). The run stops before touching anything if LYTEST01 already exists somewhere.
import { test, expect, type Page } from '@playwright/test'
import {
  cleanupWrite, confirm, dialog, fill, gridRow, matchesBaseline, normalize, openScreen, pick,
  runGrid, saveDialog, select,
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

async function snapshot(): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {}
  for (const t of TABLES) {
    const rows = await select('jdedwards', `SELECT * FROM ${t.from} WHERE ${t.where}`, { U: USER })
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
