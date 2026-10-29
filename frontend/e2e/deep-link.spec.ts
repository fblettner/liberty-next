// A link opened in a fresh browser (signed in, no app remembered yet): it opens its page, the app
// of the link selected — it used to land on the first app's home. A plain visit to "/" still lands
// on the home page. Read-only.
import { test, expect, type BrowserContext } from '@playwright/test'
import { BASE, login } from './support'

async function freshBrowser(context: BrowserContext) {
  const t = await login()
  await context.addInitScript((s) => {
    // signed in, English — and no `liberty.app`: the first visit of this browser
    localStorage.setItem('liberty.tokens', JSON.stringify(s)); localStorage.setItem('liberty.language', 'en')
  }, t)
  return context.newPage()
}

test('a shared /screen link opens its screen in a fresh browser', async ({ context }) => {
  const page = await freshBrowser(context)
  await page.goto(`${BASE}/screen/nomajde/jde_health`)
  await expect(page.getByText('nomajde.jde_health_get')).toBeVisible()
  await page.waitForTimeout(1500)                       // time for any redirect to happen
  expect(new URL(page.url()).pathname).toBe('/screen/nomajde/jde_health')
  expect(await page.evaluate(() => localStorage.getItem('liberty.app'))).toBe('nomajde')
})

test('a plain visit to / still lands on the home page', async ({ context }) => {
  const page = await freshBrowser(context)
  await page.goto(`${BASE}/`)
  await expect(page).not.toHaveURL(`${BASE}/`, { timeout: 15_000 })   // redirected to the app's home
})
