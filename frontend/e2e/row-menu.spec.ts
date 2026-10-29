// nomasx1 → Security → Users: the grid's row menu (right-click) — "Display Roles" navigates to
// the role assignments of the clicked user (JDE, application 1). Read-only: the screen that opens
// must list exactly what the assignments query returns for that user (checked live — the data
// changes with each collection).
import { test, expect } from '@playwright/test'
import { openScreen, screenRows } from './support'

const USER = 'JDE'

test('security_users: row menu → Display Roles opens the user\'s assignments', async ({ context }) => {
  const expected = (await screenRows('nomasx1', 'security_assignments', 'nomasx1', 'security_assignments_get',
    { RLU_APPS_ID: '1', RLU_APPS_ID_op: 'equals', RLU_USER_ID: USER, RLU_USER_ID_op: 'equals' }))
    .map((r) => String(r.rlu_role_id).trim())
  expect(expected.length, `${USER} has no role in application 1`).toBeGreaterThan(0)

  const page = await openScreen(context, 'nomasx1', 'security_users')
  // The row whose cells read exactly "1" (application) and "JDE" (user) — not JDEAR, JDE-DEFAULT…
  const exact = (s: string) => page.locator('td', { hasText: new RegExp(`^\\s*${s}\\s*$`) })
  const row = page.locator('tbody tr').filter({ has: exact(USER) }).filter({ has: exact('1') }).first()
  await row.click({ button: 'right' })
  await page.getByText('Display Roles', { exact: true }).click()

  // The assignments screen opens, filtered on the user: exactly the expected roles.
  await expect(page.getByText('nomasx1.security_assignments_get')).toBeVisible()
  const rows = page.locator('tbody tr:visible').filter({ has: exact(USER) })
  await expect(rows).toHaveCount(expected.length)
  for (const role of expected) await expect(rows.filter({ has: exact(role) })).toHaveCount(1)
  await expect(page.getByText('No rows.')).toHaveCount(0)
})
