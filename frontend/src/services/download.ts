// POST a request whose answer is a file (an export) and hand it to the browser as a download.
// The file name comes from Content-Disposition, else *fallback*.
import { apiDownload } from '../api/client'

export function postDownload(url: string, body: unknown, fallback: string): Promise<void> {
  return apiDownload(url, fallback, { json: body })
}
