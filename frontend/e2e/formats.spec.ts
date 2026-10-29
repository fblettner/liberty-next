// The url / email / color formats in the grid. No data of this install uses them yet, so the grid's
// rows are served by the test (the screen's read request answered with an NDJSON stream): a link
// opens in a new tab, an e-mail is a mailto, a colour shows its swatch — and a ``javascript:``
// "url" or a non-colour stays plain text. Read-only.
import { test, expect } from '@playwright/test'
import { openScreen } from './support'

test('url / email / color: link, mailto and swatch in the grid; unsafe values stay text', async ({ context }) => {
  await context.route(/\/api\/sql\/nomasx1\/[^/?]+\?.*_stream=1/, async (route) => {
    const columns = [
      { name: 'LDAP_NAME', type: null, label: 'Name' },
      { name: 'LDAP_MAIL', type: null, label: 'Mail', format: 'email' },
      { name: 'SITE', type: null, label: 'Site', format: 'url' },
      { name: 'TINT', type: null, label: 'Tint', format: 'color' },
    ]
    const rows = [
      { LDAP_NAME: 'ok', LDAP_MAIL: 'jane.doe@nomana-it.fr', SITE: 'www.oracle.com', TINT: '#1e88e5' },
      { LDAP_NAME: 'unsafe', LDAP_MAIL: 'not-an-address', SITE: 'javascript:alert(1)', TINT: 'red; background: url(x)' },
    ]
    const body = [
      { kind: 'meta', columns, cap: 1000, chunk_size: 500 },
      { kind: 'rows', rows, sent: rows.length },
      { kind: 'done', total: rows.length, truncated: false, duration_ms: 1 },
    ].map((l) => JSON.stringify(l)).join('\n') + '\n'
    await route.fulfill({ status: 200, contentType: 'application/x-ndjson', body })
  })
  const page = await openScreen(context, 'nomasx1', 'ldap_users')
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  const ok = page.locator('tbody tr').filter({ hasText: 'ok' }).first()
  await expect(ok.locator('a[href="mailto:jane.doe@nomana-it.fr"]')).toHaveText('jane.doe@nomana-it.fr')
  const site = ok.locator('a[href="https://www.oracle.com/"]')
  await expect(site).toHaveAttribute('target', '_blank')
  await expect(site).toHaveAttribute('rel', /noopener/)
  await expect(ok.locator('span[style*="background"]')).toHaveCount(1)
  // the unsafe row: no link, no swatch — the raw text
  const bad = page.locator('tbody tr').filter({ hasText: 'unsafe' }).first()
  await expect(bad.locator('a')).toHaveCount(0)
  await expect(bad.locator('span[style*="background"]')).toHaveCount(0)
  await expect(bad).toContainText('javascript:alert(1)')
  await page.screenshot({ path: test.info().outputPath('formats.png') })
})
