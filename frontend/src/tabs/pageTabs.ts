// Framework "page" routes that open as workspace TABS (not as an <Outlet/> view that replaces the
// active tab). These are the targets of menus.toml ``type = "page"`` items — the nomaflow area + the
// reports list. Keeping the set in one place keeps App (the routes), Layout (the Outlet-vs-TabHost
// decision) and TabHost (the path → component registry) in agreement. The job editor
// (/nomaflow/jobs/:id) is a page tab too (JOB_EDITOR_PATH below); run detail (/nomaflow/runs/:id — the list /nomaflow/runs
// is here) is its own ``nomaflow_run`` tab kind.
import { libertyPageOf } from '../services/libertyMenu'

// The Liberty Next workspace's pages (`/liberty/<page>`, services/libertyMenu.ts) are page tabs too.
export const PAGE_TAB_PATHS = [
  '/nomaflow',
  '/nomaflow/schedule',
  '/nomaflow/runs',
  '/reports',
  '/notifications',
  '/explorer',
  '/developer',
] as const

/** The Explorer opened on one model: ``/explorer/<app>/<model>`` (a menu link per model) — and, published in a
 *  menu, on one saved analysis (``…/a/<id>``) or dashboard (``…/d/<id>``). */
export const EXPLORER_MODEL_PATH = /^\/explorer\/([^/]+)\/([^/]+)(?:\/([ad])\/([^/]+))?$/

/** A page an application ships: ``/app/<app>/<page>`` (liberty/web/app_pages.py — the same names as its `ROUTE`). */
export const APP_PAGE_PATH = /^\/app\/([a-z0-9][a-z0-9_]*)\/([a-z0-9][a-z0-9_]*)$/

/** The job editor: ``/nomaflow/jobs/<id>`` — or ``/nomaflow/jobs/new`` for a job being created. A tab of its own,
 *  so its unsaved edits are kept when the user goes elsewhere, like every other editor's. */
export const JOB_EDITOR_PATH = /^\/nomaflow\/jobs\/([^/]+)$/

/** The job a path edits: its id, ``''`` for a new job; null when the path is not the editor's. */
export function jobEditorOf(pathname: string): string | null {
  const m = JOB_EDITOR_PATH.exec(pathname)
  if (!m) return null
  try { return m[1] === 'new' ? '' : decodeURIComponent(m[1]) } catch { return m[1] }
}

export function isPageTabPath(pathname: string): boolean {
  return (PAGE_TAB_PATHS as readonly string[]).includes(pathname) || EXPLORER_MODEL_PATH.test(pathname) || APP_PAGE_PATH.test(pathname) || JOB_EDITOR_PATH.test(pathname) || libertyPageOf(pathname) !== null
}

/** The ``{app, page}`` of an application's page path, else null. */
export function appPageOf(pathname: string): { app: string; page: string } | null {
  const m = APP_PAGE_PATH.exec(pathname)
  return m ? { app: m[1], page: m[2] } : null
}

/** The function a page asks for, as the server names it: ``module:function`` is the application's own
 *  (``<app>.module:function``); a reference that already names its package (a dot before the colon) is kept. */
export function callableRef(app: string, fn: string): string {
  const [mod = '', name = ''] = fn.split(':')
  return mod.includes('.') ? `${mod}:${name}` : `${app}.${mod}:${name}`
}

/** The ``{app, model}`` of an Explorer model path — with the saved ``analysis`` or ``dashboard`` it opens on —
 *  else null. */
export function explorerModelOf(pathname: string): { app: string; model: string; analysis?: string; dashboard?: string } | null {
  const m = EXPLORER_MODEL_PATH.exec(pathname)
  if (!m) return null
  const on = m[3] ? { [m[3] === 'a' ? 'analysis' : 'dashboard']: decodeURIComponent(m[4]) } : {}
  return { app: decodeURIComponent(m[1]), model: decodeURIComponent(m[2]), ...on }
}
