// nomajde → Monitoring → JDE Servers: every health check, the advanced filters' period on the check
// time opening on "Last 7 days" (the column's filter_period). Read-only: the grid's row count is
// compared live with the screen's query for the same period, then for all dates.
import { test, expect, type Page } from '@playwright/test'
import { openScreen, runGrid, screenRows } from './support'

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

/** The "<n> rows" count of the grid's meta line. */
async function gridCount(page: Page): Promise<number> {
  const text = await page.getByText(/^\d+ rows? ·/).first().textContent()
  return Number((text ?? '').match(/^(\d+)/)?.[1] ?? NaN)
}

test('JDE Servers: every check, the period opens on the last 7 days and can widen to all dates', async ({ context }) => {
  const today = new Date()
  const from = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6)
  const query = (params: Record<string, string>) => screenRows('nomajde', 'jde_health', 'nomajde', 'jde_health_get', params)
  const last7 = await query({ checked_at_from: iso(from), checked_at_to: iso(today) })
  const all = await query({})

  const page = await openScreen(context, 'nomajde', 'jde_health')
  // The first (auto-load) run already applies the default period.
  await expect(page.getByText(/^\d+ rows? ·/).first()).toBeVisible()
  expect(await gridCount(page)).toBe(last7.length)

  await page.getByText(/^Advanced filters/).click()   // "Advanced filters (1)": the period is set
  const trigger = page.locator('button[aria-haspopup="dialog"]').first()
  await expect(trigger).not.toHaveText('All dates')
  await trigger.click()
  await page.getByRole('button', { name: 'All dates', exact: true }).click()
  await expect(trigger).toHaveText('All dates')
  await runGrid(page)
  expect(await gridCount(page)).toBe(all.length)

  // A period that leaves rows out (the last month) — so the filter's effect is seen whatever the data.
  const lastMonth = await query({
    checked_at_from: iso(new Date(today.getFullYear(), today.getMonth() - 1, 1)),
    checked_at_to: iso(new Date(today.getFullYear(), today.getMonth(), 0)),
  })
  expect(lastMonth.length, 'the last month must differ from all dates for this check to mean something').not.toBe(all.length)
  await trigger.click()
  await page.getByRole('button', { name: 'Last month', exact: true }).click()
  await runGrid(page)
  await page.screenshot({ path: test.info().outputPath('jde-servers.png') })
  expect(await gridCount(page)).toBe(lastMonth.length)

  // One day on a timestamp column (Today: from = to = today): the whole day, not just its 00:00.
  const oneDay = await query({ checked_at_from: iso(today), checked_at_to: iso(today) })
  await trigger.click()
  await page.getByRole('button', { name: 'Today', exact: true }).click()
  await runGrid(page)
  expect(await gridCount(page)).toBe(oneDay.length)
})
