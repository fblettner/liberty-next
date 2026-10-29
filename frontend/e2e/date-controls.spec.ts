// The date controls, read-only (nothing is saved): one day picked in the period calendar on a
// date column holding a time (nomasx1 Audit - Lookup), zoned date-times shown in local time
// (nomajde JDE Servers), and the record form's themed date picker (nomasx1 License - Contracts:
// pick, type, a bad date reverts — then closed without saving, the row checked unchanged).
import { test, expect, type Page } from '@playwright/test'
import { dialog, openScreen, runGrid, screenRows, select } from './support'

async function gridCount(page: Page): Promise<number> {
  const text = await page.getByText(/^\d+ rows? ·/).first().textContent()
  return Number((text ?? '').match(/^(\d+)/)?.[1] ?? NaN)
}

test('Audit - Lookup: one day picked in the calendar keeps that whole day', async ({ context }) => {
  // the day of the first audited change, and what the query returns for that one day
  const [first] = await screenRows('nomasx1', 'audit_lookup', 'nomasx1', 'audit_lookup_get')
  test.skip(!first, 'no audit rows')
  const day = String(first.aud_dt_transaction ?? first.AUD_DT_TRANSACTION).slice(0, 10)
  const expected = await screenRows('nomasx1', 'audit_lookup', 'nomasx1', 'audit_lookup_get',
    { AUD_DT_TRANSACTION_from: day, AUD_DT_TRANSACTION_to: day })
  expect(expected.length).toBeGreaterThan(0)

  const page = await openScreen(context, 'nomasx1', 'audit_lookup')
  const panel = page.getByText(/^Advanced filters/)
  if (!(await page.locator('button[aria-haspopup="dialog"]').first().isVisible())) await panel.click()
  await page.locator('button[aria-haspopup="dialog"]').first().click()
  // the right-hand month is today's; go back until the day's month shows, then pick it twice (a one-day range)
  const [y, m, d] = day.split('-').map(Number)
  const title = new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric' }).format(new Date(y, m - 1, 1))
  // the month: the block holding its title and its day buttons (its header row has the title only)
  const month = page.locator('div').filter({ has: page.getByText(title, { exact: true }) })
    .filter({ has: page.getByRole('button', { name: String(d), exact: true }) }).last()
  // the month / year chooser of the right-hand calendar: the year by its arrows, then the month
  await page.getByRole('button', { name: 'Choose the month and year' }).last().click()
  const yearBtn = page.getByRole('button', { name: 'Choose the year' })
  for (let i = 0; i < 20 && Number(await yearBtn.textContent()) > y; i++) await page.getByRole('button', { name: 'Previous year' }).click()
  for (let i = 0; i < 20 && Number(await yearBtn.textContent()) < y; i++) await page.getByRole('button', { name: 'Next year' }).click()
  await page.getByRole('button', { name: new Intl.DateTimeFormat('en', { month: 'short' }).format(new Date(y, m - 1, 1)), exact: true }).click()
  const dayBtn = month.getByRole('button', { name: String(d), exact: true }).first()
  await dayBtn.click()
  await dayBtn.click()
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await runGrid(page)
  expect(await gridCount(page)).toBe(expected.length)
})

test('JDE Servers: a zoned check time shows in local time', async ({ context }) => {
  const [row] = await screenRows('nomajde', 'jde_health', 'nomajde', 'jde_health_get')
  test.skip(!row, 'no health check yet')
  const raw = String(row.checked_at)
  const dt = new Date(raw.replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1'))
  const p = (n: number) => String(n).padStart(2, '0')
  const local = `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}:${p(dt.getSeconds())}`
  const page = await openScreen(context, 'nomajde', 'jde_health')
  await page.locator('button[aria-haspopup="dialog"]').first().waitFor({ state: 'attached' }).catch(() => undefined)
  // all dates, so the newest check (the first row of the query) is the grid's first row
  if (!(await page.locator('button[aria-haspopup="dialog"]').first().isVisible())) await page.getByText(/^Advanced filters/).click()
  await page.locator('button[aria-haspopup="dialog"]').first().click()
  await page.getByRole('button', { name: 'All dates', exact: true }).click()
  await runGrid(page)
  await expect(page.locator('tbody tr').first()).toContainText(local)
  await expect(page.locator('tbody')).not.toContainText('+00:00')
})

test('License - Contracts: the form’s date picker picks and types a date; nothing saved', async ({ context }) => {
  const before = await select('nomasx1', 'SELECT csi_id, csi_from_date, csi_to_date FROM license_csi ORDER BY csi_id')
  test.skip(before.length === 0, 'no contract')
  const page = await openScreen(context, 'nomasx1', 'license_csi')
  await page.locator('tbody tr').first().click()
  const d = dialog(page)
  const field = d.locator('div:has(> label:text-is("From Date"))').last().locator('input')
  const original = await field.inputValue()
  expect(original).toMatch(/^\d{4}-\d{2}-\d{2}$/)

  // the calendar: the shown month is the value's; the 1st of it becomes the value
  await d.getByRole('button', { name: 'Pick a date' }).first().click()
  await page.getByRole('dialog', { name: 'Pick a date' }).getByRole('button', { name: '1', exact: true }).first().click()
  await expect(field).toHaveValue(`${original.slice(0, 8)}01`)
  // typing: an impossible day goes back to the value, a real one is taken
  await field.fill('2026-02-30')
  await field.press('Enter')
  await expect(field).toHaveValue(`${original.slice(0, 8)}01`)
  await field.fill('2026-03-15')
  await field.press('Enter')
  await expect(field).toHaveValue('2026-03-15')
  // the month / year chooser: a date a year back in three clicks (title, previous year, the month) + the day
  await d.getByRole('button', { name: 'Pick a date' }).first().click()
  const cal = page.getByRole('dialog', { name: 'Pick a date' })
  await cal.getByRole('button', { name: 'Choose the month and year' }).click()
  await cal.getByRole('button', { name: 'Previous year' }).click()
  await cal.getByRole('button', { name: 'Mar', exact: true }).click()
  await cal.getByRole('button', { name: '20', exact: true }).click()
  await expect(field).toHaveValue('2025-03-20')

  // close without saving
  await page.locator('button', { hasText: /^\s*Cancel\s*$/ }).last().click()
  const discard = page.getByRole('button', { name: 'Discard', exact: true })
  if (await discard.isVisible().catch(() => false)) await discard.click()
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0)
  expect(await select('nomasx1', 'SELECT csi_id, csi_from_date, csi_to_date FROM license_csi ORDER BY csi_id')).toEqual(before)
})

test('JDE Servers: the grid’s column filter on a date is the themed picker, on the local day', async ({ context }) => {
  const today = new Date(), p = (n: number) => String(n).padStart(2, '0')
  const iso = `${today.getFullYear()}-${p(today.getMonth() + 1)}-${p(today.getDate())}`
  const expected = await screenRows('nomajde', 'jde_health', 'nomajde', 'jde_health_get', { checked_at_from: iso, checked_at_to: iso })
  const page = await openScreen(context, 'nomajde', 'jde_health')
  if (!(await page.locator('button[aria-haspopup="dialog"]').first().isVisible())) await page.getByText(/^Advanced filters/).click()
  await page.locator('button[aria-haspopup="dialog"]').first().click()
  await page.getByRole('button', { name: 'All dates', exact: true }).click()
  await runGrid(page)
  await page.getByRole('button', { name: 'Filters', exact: true }).click()
  // the "Checked at" column's filter: a date picker (no native date input left in the grid)
  await expect(page.locator('thead input[type="date"]')).toHaveCount(0)
  const picker = page.locator('thead input[placeholder="yyyy-mm-dd"]').first()
  await picker.fill(iso)
  await picker.press('Enter')
  await expect(page.locator('tbody tr').filter({ hasNotText: iso })).toHaveCount(expected.length === 0 ? 1 : 0)
  if (expected.length) await expect(page.locator('tbody tr')).toHaveCount(expected.length)
})
