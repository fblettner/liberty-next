// nomajde → User Management (F0092): the record lock between two sessions, read-only (nothing is
// saved). A opens user JDE (takes the lock); B opens the same record → read-only, "Locked by …",
// no Save; A closes → B's form becomes editable without reopening (the release broadcast).
import { test, expect, type Page } from '@playwright/test'
import { dialog, openScreen } from './support'

const USER = 'JDE'

async function openRecord(page: Page) {
  const exact = page.locator('td', { hasText: new RegExp(`^\\s*${USER}\\s*$`) })
  await page.locator('tbody tr').filter({ has: exact }).first().click()
  await expect(dialog(page).or(page.getByText(/^Locked by /))).toBeVisible()
}

test('F0092: a record open in one session opens read-only in another, until it is closed', async ({ browser }) => {
  const a = await openScreen(await browser.newContext(), 'nomajde', 'f0092')
  const b = await openScreen(await browser.newContext(), 'nomajde', 'f0092')

  await openRecord(a)
  await expect(a.getByRole('button', { name: 'Save', exact: true })).toBeVisible()

  // B connected before A's lock: it learns of it from the broadcast — and, connected or not,
  // from the server's refusal (the 7.0.67 fix).
  await openRecord(b)
  await expect(b.getByText(/^Locked by claude/)).toBeVisible()
  await expect(b.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0)

  // A closes → the lock is released → B's form turns editable in place.
  await a.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(b.getByText(/^Locked by /)).toHaveCount(0)
  await expect(b.getByRole('button', { name: 'Save', exact: true })).toBeVisible()
  await b.getByRole('button', { name: 'Cancel', exact: true }).click()
})

test('F0092: a session that connects after the lock was taken still sees it', async ({ browser }) => {
  const a = await openScreen(await browser.newContext(), 'nomajde', 'f0092')
  await openRecord(a)
  // B's socket connects only now — it never saw A's lock.acquired broadcast.
  const b = await openScreen(await browser.newContext(), 'nomajde', 'f0092')
  await openRecord(b)
  await expect(b.getByText(/^Locked by claude/)).toBeVisible()
  await expect(b.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0)
  await a.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(b.getByRole('button', { name: 'Save', exact: true })).toBeVisible()
})
