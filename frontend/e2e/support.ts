// Shared helpers of the end-to-end scenarios: sign-in, database reads through the superuser SQL
// route (the SQL editor's Run — SELECTs only here), the record form / grid helpers, and the
// before / after snapshot compared with a recorded reference.
import { expect, type Page, type BrowserContext, type Locator } from '@playwright/test'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const BASE = process.env.LIBERTY_E2E_URL ?? 'http://127.0.0.1:8000'
const USER = process.env.LIBERTY_E2E_USER
const PASSWORD = process.env.LIBERTY_E2E_PASSWORD
// LIBERTY_E2E_RECORD=1 writes the reference rows instead of comparing with them.
const RECORD = process.env.LIBERTY_E2E_RECORD === '1'
const BASELINE_DIR = join(dirname(fileURLToPath(import.meta.url)), '.baseline')

type Tokens = { access_token: string; refresh_token: string }
let tokens: Tokens | null = null

export async function login(): Promise<Tokens> {
  if (tokens) return tokens
  if (!USER || !PASSWORD) throw new Error('Set LIBERTY_E2E_USER and LIBERTY_E2E_PASSWORD')
  const r = await fetch(`${BASE}/auth/login`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ username: USER, password: PASSWORD }),
  })
  if (!r.ok) throw new Error(`login failed: ${r.status}`)
  tokens = (await r.json()) as Tokens
  return tokens
}

/** Signed in, English, the screen's app picked — then the screen opened. */
export async function openScreen(context: BrowserContext, app: string, screen: string): Promise<Page> {
  const t = await login()
  await context.addInitScript((s) => {
    localStorage.setItem('liberty.tokens', JSON.stringify(s.t))
    localStorage.setItem('liberty.language', 'en')
    localStorage.setItem('liberty.app', s.app)
  }, { t, app })
  const page = await context.newPage()
  await page.goto(`${BASE}/screen/${app}/${screen}`)
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeVisible()
  return page
}

// ── database ────────────────────────────────────────────────────────────────────────────────

export type DbRow = Record<string, unknown>

/** Run a SELECT on *connector*'s pool (``#SCHEMA.X#`` / ``#DBLINK.X#`` resolved by the server). */
export async function select(connector: string, sql: string, params: Record<string, unknown> = {}): Promise<DbRow[]> {
  if (!/^\s*select\b/i.test(sql)) throw new Error('select() runs SELECTs only')
  return runSql(connector, sql, params, true)
}

/** What a screen's read query returns (the screen's own filters / scope applied by the server). */
export async function screenRows(app: string, screen: string, connector: string, query: string,
                                 params: Record<string, string> = {}): Promise<DbRow[]> {
  const t = await login()
  const qs = new URLSearchParams({ ...params, _screen: screen, _app: app }).toString()
  const r = await fetch(`${BASE}/api/sql/${connector}/${query}?${qs}`, { headers: { authorization: `Bearer ${t.access_token}` } })
  if (!r.ok) throw new Error(`${connector}.${query} failed: ${r.status}`)
  return ((await r.json()) as { rows: DbRow[] }).rows
}

/** A write run for real — kept for the cleanup of the test's own rows after a failed run. */
export async function cleanupWrite(connector: string, sql: string, params: Record<string, unknown>): Promise<void> {
  await runSql(connector, sql, params, false)
}

async function runSql(connector: string, sql: string, params: Record<string, unknown>, dryRun: boolean): Promise<DbRow[]> {
  const t = await login()
  const r = await fetch(`${BASE}/admin/config/connectors/${connector}/test-sql`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${t.access_token}` },
    body: JSON.stringify({ sql, params, max_rows: 500, dry_run: dryRun }),
  })
  const body = await r.json() as { columns?: (string | { name: string })[]; rows?: unknown[][] | DbRow[]; detail?: unknown }
  if (!r.ok) throw new Error(`SQL on ${connector} failed (${r.status}): ${JSON.stringify(body.detail)}`)
  const cols = (body.columns ?? []).map((c) => (typeof c === 'string' ? c : c.name).toLowerCase())
  return (body.rows ?? []).map((row) => {
    if (!Array.isArray(row)) return Object.fromEntries(Object.entries(row).map(([k, v]) => [k.toLowerCase(), v]))
    return Object.fromEntries(cols.map((c, i) => [c, row[i]]))
  })
}

/** Rows ready to compare: the volatile / secret columns dropped, strings trimmed (JDE pads). */
export function normalize(rows: DbRow[], drop: string[]): DbRow[] {
  const gone = new Set(drop.map((d) => d.toLowerCase()))
  return rows.map((r) => Object.fromEntries(Object.entries(r)
    .filter(([k]) => !gone.has(k))
    .map(([k, v]) => [k, typeof v === 'string' ? v.trimEnd() : v])
    .sort(([a], [b]) => String(a).localeCompare(String(b)))))
}

/** Compare *actual* with the recorded reference *name* (or record it with LIBERTY_E2E_RECORD=1). */
export function matchesBaseline(name: string, actual: unknown): void {
  const file = join(BASELINE_DIR, `${name}.json`)
  if (RECORD || !existsSync(file)) {
    mkdirSync(BASELINE_DIR, { recursive: true })
    writeFileSync(file, JSON.stringify(actual, null, 2) + '\n')
    return
  }
  expect(actual, `rows differ from the reference ${file}`).toEqual(JSON.parse(readFileSync(file, 'utf8')))
}

// ── record form ─────────────────────────────────────────────────────────────────────────────

/** The open record form: the innermost block holding field labels and the Save / Cancel buttons
 *  (the modal renders last in the page, so ``last()`` is the form, not an outer wrapper). */
export function dialog(page: Page): Locator {
  return page.locator('div')
    .filter({ has: page.locator('label') })
    .filter({ has: page.getByRole('button', { name: 'Save', exact: true }) })
    .filter({ has: page.getByRole('button', { name: 'Cancel', exact: true }) })
    .last()
}

/** The control under the field label *label* (a form field is ``<label>`` + its input / dropdown). */
export function field(scope: Locator, label: string): Locator {
  return scope.locator(`div:has(> label:text-is("${label}"))`).last()
}

export async function fill(scope: Locator, label: string, value: string): Promise<void> {
  const input = field(scope, label).locator('input, textarea').first()
  await input.fill(value)
}

/** Pick *option* in the dropdown (the shared SearchSelect) of field *label*. */
export async function pick(page: Page, scope: Locator, label: string, option: string): Promise<void> {
  await field(scope, label).locator('button').first().click()
  await chooseOption(page, option)
}

/** In a SearchSelect list just opened: type in its search box, then click the option whose text
 *  starts with *option* (an option reads "<code> <description>"). The list renders at the end of
 *  the page, so it is the last block holding a search box and buttons — never the grid behind it,
 *  whose cells show the same codes. */
export async function chooseOption(page: Page, option: string): Promise<void> {
  // The list is a portal: a direct child of <body>, outside the app's #root (whose grid toolbar
  // has its own "Search…" box — typing there would filter the grid).
  const list = page.locator('body > div:not(#root)')
    .filter({ has: page.getByPlaceholder('Search…', { exact: true }) })
    .last()
  await list.getByPlaceholder('Search…', { exact: true }).fill(option)
  // An option shows its code and its description in two elements: match the code exactly (P0092
  // must not pick P00921), else an option whose whole text is *option*.
  // A lookup over a remote database (JDE objects…) can take a few seconds to load its options.
  await list.locator('button').filter({ has: page.getByText(option, { exact: true }) }).first().click({ timeout: 20_000 })
}

export async function clickTab(scope: Locator, label: string): Promise<void> {
  await scope.getByText(label, { exact: true }).first().click()
}

/** Save the form and wait for it to close (a failed save keeps it open → the test fails here). */
export async function saveDialog(page: Page): Promise<void> {
  const d = dialog(page)
  await d.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0, { timeout: 10_000 })
}

/** Answer an open confirmation (the app's own modal) with *button*. */
export async function confirm(page: Page, button: string): Promise<void> {
  await page.getByRole('button', { name: button, exact: true }).last().click()
}

/** The grid row whose cells include *text*. */
export function gridRow(page: Page, text: string): Locator {
  return page.locator('tbody tr').filter({ hasText: text })
}

/** Run the screen's query and wait for its rows (a click on the grid while it reloads would land
 *  on a row that is about to be replaced). */
export async function runGrid(page: Page): Promise<void> {
  const done = page.waitForResponse((r) => r.url().includes('/api/sql/') && r.request().method() === 'GET')
  await page.getByRole('button', { name: 'Run', exact: true }).click()
  await (await done).finished()
  await page.waitForTimeout(500)
}

// ── bulk edit ───────────────────────────────────────────────────────────────────────────────

export async function startBulkEdit(page: Page): Promise<void> {
  await runGrid(page)
  await page.getByRole('button', { name: 'Bulk edit', exact: true }).click()
  await expect(page.getByRole('button', { name: /^Save/ }).first()).toBeVisible()
}

/** The new rows of the bulk edit (marked "+"), the most recent first. */
export function newRows(page: Page): Locator {
  return page.locator('tbody tr').filter({ has: page.locator('[title="new row"]') })
}

/** Save the bulk edit — after checking that every edited or deleted row carries *marker* in one of
 *  its inputs (a test row). Anything else → Cancel and fail: an existing row is never saved. */
export async function saveBulkEdit(page: Page, marker: string): Promise<void> {
  const touched = page.locator('tbody tr').filter({ has: page.locator('[title="edited"], [title="marked for deletion"]') })
  // A row's values: its inputs' values and its cells' text (a dropdown cell shows its code as text).
  const values = await touched.evaluateAll((rows) => rows.map((r) =>
    [...r.querySelectorAll('input')].map((i) => (i as HTMLInputElement).value).join(' | ') + ' | ' + (r.textContent ?? '')))
  const foreign = values.filter((v) => !v.includes(marker))
  if (foreign.length) {
    await page.getByRole('button', { name: 'Cancel', exact: true }).first().click()
    throw new Error(`bulk edit touched rows that are not test rows — cancelled, nothing saved: ${foreign.join(' / ')}`)
  }
  const done = page.waitForResponse((r) => r.url().includes('/api/sql/') && r.request().method() === 'POST')
  await page.getByRole('button', { name: /^Save/ }).first().click()
  await done
  await expect(page.getByRole('button', { name: 'Bulk edit', exact: true })).toBeVisible({ timeout: 10_000 })
}
