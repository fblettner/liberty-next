// A page an application ships (plugins/<app>/pages/<page>.js, served by liberty/web/app_pages.py):
// fetched with the session, imported as a module, and drawn with what sdk.ts hands it. The tab's
// ErrorBoundary (TabHost) catches what the page throws while it draws.
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { useEffect, useState, type ComponentType } from 'react'
import { useTranslation } from 'react-i18next'
import { FileCode2 } from 'lucide-react'
import { apiFetch } from '../../api/client'
import { Banner, Centered, PageLayout } from '../../common'
import { buildSdk } from './sdk'

type Loaded = { Page: ComponentType } | { problem: 'missing' | 'forbidden' | 'broken'; detail?: string }

// one load per page for the session: reopening its tab draws at once (a reload fetches it again)
const loads = new Map<string, Promise<Loaded>>()

async function load(app: string, page: string): Promise<Loaded> {
  const res = await apiFetch(`/api/pages/${encodeURIComponent(app)}/${encodeURIComponent(page)}`)
  if (res.status === 404) return { problem: 'missing' }
  if (res.status === 403) return { problem: 'forbidden' }
  if (!res.ok) return { problem: 'broken', detail: `HTTP ${res.status}` }
  const url = URL.createObjectURL(new Blob([await res.text()], { type: 'text/javascript' }))
  try {
    const mod = (await import(/* @vite-ignore */ url)) as { default?: unknown }
    if (typeof mod.default !== 'function') return { problem: 'broken', detail: 'the module has no default export function' }
    const Page = (mod.default as (sdk: ReturnType<typeof buildSdk>) => unknown)(buildSdk(app, page))
    if (typeof Page !== 'function') return { problem: 'broken', detail: 'the page function did not return a component' }
    return { Page: Page as ComponentType }
  } catch (e) {
    return { problem: 'broken', detail: e instanceof Error ? e.message : String(e) }
  } finally {
    URL.revokeObjectURL(url)
  }
}

export default function AppPage({ app, page }: { app: string; page: string }) {
  const { t } = useTranslation()
  const { currentPool } = useWorkspace()
  const key = `${app}/${page}`
  const [loaded, setLoaded] = useState<{ key: string; value: Loaded } | null>(null)
  useEffect(() => {
    let cancelled = false
    let p = loads.get(key)
    if (!p) { p = load(app, page); loads.set(key, p) }
    void p.then((value) => {
      if ('problem' in value) loads.delete(key)             // a page that failed is asked again next time
      if (!cancelled) setLoaded({ key, value })
    })
    return () => { cancelled = true }
  }, [app, page, key])
  const value = loaded?.key === key ? loaded.value : null
  // the page reads the environment (pool) picked for the application: another one picked, it starts again on it
  if (value && 'Page' in value) return <value.Page key={currentPool ?? ''} />
  return (
    <PageLayout icon={<FileCode2 size={18} />} title={key}>
      {!value ? <Centered /> : (
        <Banner $tone={value.problem === 'forbidden' ? 'warning' : 'error'} data-app-page-problem={value.problem}>
          {t(`appPage.${value.problem}`, { app, page })}{value.detail ? ` — ${value.detail}` : ''}
        </Banner>
      )}
    </PageLayout>
  )
}
