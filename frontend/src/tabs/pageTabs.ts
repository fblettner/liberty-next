// Framework "page" routes that open as workspace TABS (not as an <Outlet/> view that replaces the
// active tab). These are the targets of menus.toml ``type = "page"`` items — the nomaflow area + the
// reports list. Keeping the set in one place keeps App (the routes), Layout (the Outlet-vs-TabHost
// decision) and TabHost (the path → component registry) in agreement. The job editor
// (/nomaflow/jobs/:id) and run detail (/nomaflow/runs/:id) are intentionally NOT here: the editor is
// a transient full-page reached from a button, and run detail is its own ``nomaflow_run`` tab kind.
export const PAGE_TAB_PATHS = [
  '/nomaflow',
  '/nomaflow/schedule',
  '/nomaflow/package',
  '/nomaflow/changes',
  '/nomaflow/integrity',
  '/reports',
  '/notifications',
  '/explorer',
] as const

/** The Explorer opened on one model: ``/explorer/<app>/<model>`` (a menu link per model). */
export const EXPLORER_MODEL_PATH = /^\/explorer\/([^/]+)\/([^/]+)$/

export function isPageTabPath(pathname: string): boolean {
  return (PAGE_TAB_PATHS as readonly string[]).includes(pathname) || EXPLORER_MODEL_PATH.test(pathname)
}

/** The ``{app, model}`` of an Explorer model path, else null. */
export function explorerModelOf(pathname: string): { app: string; model: string } | null {
  const m = EXPLORER_MODEL_PATH.exec(pathname)
  return m ? { app: decodeURIComponent(m[1]), model: decodeURIComponent(m[2]) } : null
}
