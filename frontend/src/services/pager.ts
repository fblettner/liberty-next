// A pager's figures for rows paged by the server: how many pages, and which rows the page in view holds.
// No React — the shared grid draws it (common/DataTable `serverPaging`).

export interface PageWindow { page: number; pages: number; from: number; to: number; total: number }

/** *page* is 1-based and kept inside 1..pages; an empty result is one page showing 0–0. */
export function pageWindow(page: number, pageSize: number, total: number): PageWindow {
  const size = Math.max(1, Math.floor(pageSize) || 1)
  const all = Math.max(0, Math.floor(total) || 0)
  const pages = Math.max(1, Math.ceil(all / size))
  const p = Math.min(pages, Math.max(1, Math.floor(page) || 1))
  return { page: p, pages, from: all === 0 ? 0 : (p - 1) * size + 1, to: Math.min(p * size, all), total: all }
}
