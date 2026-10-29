// nomasx1 → Settings → Applications: the record form (an application on four tabs, the JD
// Edwards tab saved to SETTINGS_JDEDWARDS), Duplicate, Delete, and the grid's bulk edit (add,
// change, clone, delete). Only rows named LYTEST-… are created, changed and deleted — the run
// stops before touching anything if one already exists.
import { test, expect, type Page } from '@playwright/test'
import {
  chooseOption, cleanupWrite, clickTab, confirm, dialog, field, fill, gridRow, matchesBaseline, normalize,
  newRows, openScreen, pick, runGrid, saveBulkEdit, saveDialog, select, startBulkEdit,
} from './support'

const PREFIX = 'LYTEST-'
const CLONE_ID = 9901

async function snapshot() {
  const apps = await select('nomasx1',
    "SELECT * FROM settings_applications WHERE apps_name LIKE 'LYTEST-%' ORDER BY apps_name")
  const names = new Map(apps.map((a) => [a.apps_id, a.apps_name]))
  const ids = [...names.keys()].map(Number).join(',')
  const jde = names.size === 0 ? [] : await select('nomasx1', `SELECT * FROM settings_jdedwards WHERE apps_id IN (${ids})`)
  const taskViews = names.size === 0 ? [] : await select('nomasx1',
    `SELECT * FROM settings_jde_tv WHERE tv_apps_id IN (${ids}) ORDER BY tv_apps_id, tv_task_id`)
  // The ids come from a sequence: the rows are compared by name, not by id.
  return {
    applications: normalize(apps, ['apps_id', 'apps_password', 'apps_audit_date']),
    jdedwards: normalize(jde.map((j) => ({ ...j, apps_id: names.get(j.apps_id) }))
      .sort((a, b) => String(a.apps_id).localeCompare(String(b.apps_id))), ['jde_audit_date']),
    taskViews: normalize(taskViews.map((v) => ({ ...v, tv_apps_id: names.get(v.tv_apps_id) }))
      .sort((a, b) => `${a.tv_apps_id}|${a.tv_task_id}`.localeCompare(`${b.tv_apps_id}|${b.tv_task_id}`)), ['tv_audit_date']),
  }
}

async function cleanup() {
  await cleanupWrite('nomasx1',
    "DELETE FROM settings_jde_tv WHERE tv_apps_id IN (SELECT apps_id FROM settings_applications WHERE apps_name LIKE 'LYTEST-%')", {})
  await cleanupWrite('nomasx1',
    "DELETE FROM settings_jdedwards WHERE apps_id IN (SELECT apps_id FROM settings_applications WHERE apps_name LIKE 'LYTEST-%')", {})
  await cleanupWrite('nomasx1', "DELETE FROM settings_applications WHERE apps_name LIKE 'LYTEST-%'", {})
}

async function openRow(page: Page, name: string) {
  await gridRow(page, name).first().click()
  await expect(dialog(page)).toBeVisible()
}

test('settings_applications: form add / duplicate / delete, bulk add / change / clone / delete', async ({ context }) => {
  const before = await snapshot()
  expect(before.applications, 'LYTEST-… rows already exist — clean them first').toEqual([])
  let failed = true
  try {
    const page = await openScreen(context, 'nomasx1', 'settings_applications')

    await test.step('form: add an application with its JD Edwards tab', async () => {
      await page.getByTitle('Open the form to add a new row').click()
      const d = dialog(page)
      await fill(d, 'Application Name', `${PREFIX}FORM`)
      await pick(page, d, 'Appplication Type', 'JDE')
      await pick(page, d, 'Database', 'ORACLE')
      await clickTab(d, 'Connexion')
      await fill(d, 'Host', 'lytest.invalid')
      await fill(d, 'User', 'lytest')
      await fill(d, 'Password', 'e2e-not-a-secret')
      await clickTab(d, 'JD Edwards')
      await fill(d, 'JDE DTA', 'TESTDTA')
      await fill(d, 'JDE CTL', 'TESTCTL')
      await fill(d, 'JDE SY', 'SY920')
      await fill(d, 'OUT Retention Days', '30')
      await saveDialog(page)
      matchesBaseline('sa-1-form-add', await snapshot())
    })

    await test.step('form: a row in its Task Views sub-table (task 91 "test", Collect checked)', async () => {
      await runGrid(page)
      await openRow(page, `${PREFIX}FORM`)
      await clickTab(dialog(page), 'Task Views')
      await page.getByRole('button', { name: 'Add row', exact: true }).last().click()
      const sub = dialog(page)   // the sub-table's own form, over the application's
      await fill(sub, 'Task ID', '91')
      await fill(sub, 'Task Name', 'test')
      await field(sub, 'Collect Security (Y/N)').locator('input[type="checkbox"]').check()
      await sub.getByRole('button', { name: 'Save', exact: true }).click()
      await expect(page.locator('tbody tr').filter({ hasText: 'test' }).filter({ hasText: '91' })).toBeVisible()
      await page.locator('button', { hasText: /^\s*Cancel\s*$/ }).last().click()
      await expect(page.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0)
      matchesBaseline('sa-1b-form-subtable', await snapshot())
    })

    await test.step('form: duplicate it', async () => {
      await runGrid(page)
      await openRow(page, `${PREFIX}FORM`)
      await dialog(page).getByRole('button', { name: 'Duplicate', exact: true }).click()
      await fill(dialog(page), 'Application Name', `${PREFIX}DUP`)
      await saveDialog(page)
      matchesBaseline('sa-2-form-duplicate', await snapshot())
    })

    await test.step('bulk edit: add a row', async () => {
      await startBulkEdit(page)
      await page.getByRole('button', { name: 'Add row', exact: true }).click()
      const row = newRows(page).first()
      await expect(row).toBeVisible()
      // Typed key by key: a cell that re-mounted on each edit would lose the focus mid-word and the
      // saved name would come out cut (the grid keeps its cells stable on purpose).
      await row.locator('input[type="text"]').first().pressSequentially(`${PREFIX}BULK`)
      await row.locator('button:not([title])').first().click()  // the type dropdown (row buttons have a title)
      await chooseOption(page, 'JDE')
      await saveBulkEdit(page, PREFIX)
      matchesBaseline('sa-3-bulk-add', await snapshot())
    })

    await test.step('bulk edit: change a row, clone another', async () => {
      await startBulkEdit(page)
      const name = page.locator(`tbody input[value="${PREFIX}BULK"]`)
      await name.click()
      await name.press('End')
      await page.keyboard.type('2')
      await page.locator('tbody tr').filter({ has: page.locator(`input[value="${PREFIX}DUP"]`) }).getByTitle('Clone row').click()
      const clone = newRows(page).first()
      await expect(clone).toBeVisible()
      await clone.locator(`input[value="${PREFIX}DUP"]`).fill(`${PREFIX}CLONE`)
      // A bulk clone copies the key; an empty one gets a sequence id for the application but not for
      // its JD Edwards row (foreign-key error) — so the clone gets an explicit unused id.
      await clone.locator('input[type="number"]').first().fill(String(CLONE_ID))
      await saveBulkEdit(page, PREFIX)
      matchesBaseline('sa-4-bulk-change-clone', await snapshot())
    })

    await test.step('bulk edit: delete two rows', async () => {
      await startBulkEdit(page)
      for (const name of [`${PREFIX}BULK2`, `${PREFIX}CLONE`]) {
        await page.locator('tbody tr').filter({ has: page.locator(`input[value="${name}"]`) }).getByTitle('Delete row').click()
      }
      await saveBulkEdit(page, PREFIX)
      matchesBaseline('sa-5-bulk-delete', await snapshot())
    })

    await test.step('form: delete the two rows left', async () => {
      for (const name of [`${PREFIX}DUP`, `${PREFIX}FORM`]) {
        await runGrid(page)
        await openRow(page, name)
        await dialog(page).getByRole('button', { name: 'Delete', exact: true }).click()
        await confirm(page, 'Delete')
        await expect(page.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0, { timeout: 10_000 })
      }
      const after = await snapshot()
      expect(after).toEqual({ applications: [], jdedwards: [], taskViews: [] })
    })
    failed = false
  } finally {
    if (failed) await cleanup()
  }
})
