// nomajde → User Management (F0092): a lifecycle hook that fails after the row is written. The
// on_insert action's AIS call (reset_password, stop_on_error) is answered by the test with the
// failure AIS gives when it is down — the form must stay open and say so, not close silently.
// Test user LYTEST01 only; its rows are removed at the end.
import { test, expect } from '@playwright/test'
import { cleanupWrite, dialog, fill, openScreen, select } from './support'

const USER = 'LYTEST01'

async function cleanup() {
  for (const [table, col] of [['F00921', 'ULUSER'], ['F98OWSEC', 'SCUSER'], ['F0092', 'ULUSER']]) {
    await cleanupWrite('jdedwards', `DELETE FROM #SCHEMA.SY#.${table}#DBLINK.SY# WHERE TRIM(${col}) = :U`, { U: USER })
  }
}

test('F0092: a failed AIS call in on_insert keeps the form open with the error', async ({ context }) => {
  const exists = await select('jdedwards', 'SELECT COUNT(*) N FROM #SCHEMA.SY#.F0092#DBLINK.SY# WHERE TRIM(ULUSER) = :U', { U: USER })
  expect(exists[0].n, `${USER} already exists — clean it first`).toBe(0)
  await context.route(/\/api\/http\/ais_connection\//, (r) => r.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ connector: 'ais_connection', endpoint: 'f98owsec_update_records_long', success: false, status_code: 500, error: 'Connection refused', json: null, body: null }),
  }))
  try {
    const page = await openScreen(context, 'nomajde', 'f0092')
    await page.getByTitle('Open the form to add a new row').click()
    await fill(dialog(page), 'User ID', USER)
    await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText(/The main row was saved, but a follow-up action failed: .*Connection refused/)).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeVisible()   // still open
  } finally {
    await cleanup()
  }
})
