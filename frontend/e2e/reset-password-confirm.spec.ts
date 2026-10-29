// nomajde → User Management (F0092) → Reset Password asks first, naming the user and the environment
// the screen runs on (#POOL#); Cancel sends nothing, Confirm sends the AIS call. Nothing reaches JD
// Edwards: the grid's row (test user LYTEST01) and every AIS call are served by the test.
import { test, expect } from '@playwright/test'
import { confirm, dialog, gridRow, openScreen } from './support'

const USER = 'LYTEST01'

test('F0092: Reset Password confirms with the user and the environment, Cancel sends nothing', async ({ context }) => {
  await context.route(/\/api\/sql\/jdedwards\/f0092_get\?.*_stream=1/, async (route) => {
    const rows = [{ ULUSER: USER, ULUGRP: '', ULUTYP: '', ULAN8: 0, ABALPH: 'Liberty test user' }]
    const columns = Object.keys(rows[0]).map((name) => ({ name, type: null, label: name }))
    const body = [
      { kind: 'meta', columns, cap: 1000, chunk_size: 500 },
      { kind: 'rows', rows, sent: rows.length },
      { kind: 'done', total: rows.length, truncated: false, duration_ms: 1 },
    ].map((l) => JSON.stringify(l)).join('\n') + '\n'
    await route.fulfill({ status: 200, contentType: 'application/x-ndjson', body })
  })
  const calls: { endpoint: string; body: string; pool: string | null }[] = []
  await context.route(/\/api\/http\/ais_connection\//, async (route) => {
    const req = route.request()
    calls.push({ endpoint: req.url().split('/').pop() ?? '', body: req.postData() ?? '', pool: await req.headerValue('x-liberty-pool') })
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ connector: 'ais_connection', endpoint: 'f98owsec_update_records_long', success: true, status_code: 200, json: {}, body: null }),
    })
  })

  const page = await openScreen(context, 'nomajde', 'f0092')
  await gridRow(page, USER).first().click()
  await expect(dialog(page)).toBeVisible()

  // the question names the user and an environment — never "on ?"
  await dialog(page).getByRole('button', { name: 'Reset Password', exact: true }).click()
  const question = page.getByText(new RegExp(`^Reset the password of ${USER} on [A-Za-z0-9_]+\\?$`))
  await expect(question).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('reset-confirm.png') })
  await confirm(page, 'Cancel')
  await expect(question).toHaveCount(0)
  expect(calls).toEqual([])

  // confirmed: one AIS call, for that user
  await dialog(page).getByRole('button', { name: 'Reset Password', exact: true }).click()
  await expect(question).toBeVisible()
  await confirm(page, 'Confirm')
  await expect.poll(() => calls.length).toBe(1)
  expect(calls[0].endpoint).toBe('f98owsec_update_records_long')
  expect(calls[0].body).toContain(USER)
})
