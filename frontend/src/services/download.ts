// POST a request whose answer is a file (an export) and hand it to the browser as a download.
// The file name comes from Content-Disposition, else *fallback*. Plain fetch (not the api
// wrapper, which parses JSON); the auth header as the wrapper sends it.
import { authHeaders } from '../api/client'

export async function postDownload(url: string, body: unknown, fallback: string): Promise<void> {
  const res = await fetch(url, { method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) {
    let detail = `${res.status}`
    try { detail = (await res.json()).detail ?? detail } catch { /* keep the status */ }
    throw new Error(detail)
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? fallback
  const href = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = href; a.download = name; a.click()
  URL.revokeObjectURL(href)
}
