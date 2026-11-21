// Framework "page" routes that open as workspace TABS (not as an <Outlet/> view that replaces the
// active tab). These are the targets of menus.toml ``type = "page"`` items — the nomaflow area + the
// reports list. Keeping the set in one place keeps App (the routes), Layout (the Outlet-vs-TabHost
// decision) and TabHost (the path → component registry) in agreement. The job editor
// (/nomaflow/jobs/:id) and run detail (/nomaflow/runs/:id — the list /nomaflow/runs is) are intentionally NOT here: the editor is
// a transient full-page reached from a button, and run detail is its own ``nomaflow_run`` tab kind.
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

/** The Explorer opened on one model: ``/explorer/<app>/<model>`` (a menu link per model). */
export const EXPLORER_MODEL_PATH = /^\/explorer\/([^/]+)\/([^/]+)$/

/** A page an application ships: ``/app/<app>/<page>`` (liberty/web/app_pages.py — the same names as its `ROUTE`). */
export const APP_PAGE_PATH = /^\/app\/([a-z0-9][a-z0-9_]*)\/([a-z0-9][a-z0-9_]*)$/

export function isPageTabPath(pathname: string): boolean {
  return (PAGE_TAB_PATHS as readonly string[]).includes(pathname) || EXPLORER_MODEL_PATH.test(pathname) || APP_PAGE_PATH.test(pathname) || libertyPageOf(pathname) !== null
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

/** The ``{app, model}`` of an Explorer model path, else null. */
export function explorerModelOf(pathname: string): { app: string; model: string } | null {
  const m = EXPLORER_MODEL_PATH.exec(pathname)
  return m ? { app: decodeURIComponent(m[1]), model: decodeURIComponent(m[2]) } : null
}
