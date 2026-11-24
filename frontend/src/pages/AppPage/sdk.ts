// What a page shipped by an application receives — the CONTRACT between the framework and the
// applications' pages (liberty/web/app_pages.py serves them, pages/AppPage loads them).
//
// A page is one ES module whose default export is a function: it takes this object and returns
// the React component to draw. It imports nothing: everything it is made of comes from here, so
// the page and the framework share one React, one theme and the same components.
//
//   export default function page(sdk) {
//     const { React, ui } = sdk
//     const h = React.createElement
//     return function MyPage() { return h(ui.PageLayout, { title: 'My page' }, h(ui.Card, null, '…')) }
//   }
//
// Keeping it stable: a field is added, never renamed or removed, and `version` goes up when a page
// could tell the difference. A page that needs a newer contract checks `sdk.version` and says so.
import React from 'react'
import styled from '@emotion/styled'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import * as ui from '../../common'
import { api, apiDownload } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import { iconFor } from '../../components/SidebarMenu'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'
import { callableRef } from '../../tabs/pageTabs'
import { PageGrid } from './PageGrid'
import { Diagram } from '../../common/Diagram'
import { can } from '../../services/permissions'
import { pageFacts, setPageContext } from '../../services/aiContext'

// 2: `Grid` (the shared grid) · 3: a row of the grid opens (`detail`) · 4: `useAssistantContext` · 5: `Diagram`
export const PAGE_SDK_VERSION = 7

export interface PageSdk {
  /** the contract's version (this file) */
  version: number
  /** the application and the page being drawn */
  app: string
  page: string
  /** the framework's React: hooks, createElement, Fragment */
  React: typeof React
  /** styled components (emotion), on the theme below */
  styled: typeof styled
  /** the theme's tokens — CSS variables: they follow dark / light by themselves */
  theme: { colors: typeof colors; fontSize: typeof fontSize; fonts: typeof fonts; radius: typeof radius; shadow: typeof shadow }
  /** the shared components: PageLayout, Card, Button, Input, SearchSelect, Tag, Banner, Spinner, Modal… */
  ui: typeof ui
  /** The shared grid, for a list of rows: search, filters, sort, grouping, columns, export and the user's saved
   *  views — `<Grid columns={[{ key, label, type?, mono?, render? }]} rows={rows} id="app:page:list" />` in a box
   *  that has a height. Since version 2. */
  Grid: typeof PageGrid
  /** A diagram the server laid out, drawn — the tiers as columns, the hosts as boxes, a card per node (its state, a
   *  dashed outline when it is not declared), the links on their paths, a legend:
   *  `<Diagram layout={answer.diagram} picked={id} onPick={(node) => …} words={{ ok, warn, down, unknown, undeclared }} />`
   *  in a box that has a height. `layout` is what `liberty.diagram.lay_out` answers (the application's function
   *  builds the model and returns it laid out); the same data gives the PDF's picture (`render_svg`). The words
   *  are the page's, in its language. Since version 5. */
  Diagram: typeof Diagram
  /** a menu icon by its name ("search", "database"…), or null */
  icon: typeof iconFor
  /** the API client (get / post / put / delete on the server's routes, the session handled) */
  api: typeof api
  /** Run a function of the application and get what it returns: `call('xref:j_xref_search', { q })`
   *  runs `<app>.xref:j_xref_search` — or name another package in full (`'pkg.mod:fn'`). The caller
   *  needs `plugin:<that reference>`. */
  call: <T = unknown>(fn: string, params?: Record<string, unknown>) => Promise<T>
  /** The same, for a page that reads a lot: the server KEEPS what the function answered and gives it back at once
   *  — with the moment it was computed, to be shown (`at`, an ISO date; `by`, who or which job; `fresh` = computed
   *  by this very call). `callKept(fn, params, true)` computes anew, however long, and replaces what is kept: the
   *  page's *Read again*. An answer is kept per function, parameters and data scope; a job prepares it with the
   *  step `liberty.kept:j_keep`. Since version 7. */
  callKept: <T = unknown>(fn: string, params?: Record<string, unknown>, refresh?: boolean) => Promise<KeptAnswer<T>>
  /** hook: the language of the interface ('en', 'fr'…) — the page ships its own texts */
  useLang: () => string
  /** hook: who is signed in */
  useUser: () => { username: string; is_superuser: boolean; permissions: string[] } | null
  /** hook: a function that opens a route of the application in its tab ('/screen/<app>/<id>', '/explorer'…) */
  useOpen: () => (path: string) => void
  /** Give the user a FILE a route answers (a report as PDF, an export): `download('/api/reports/<scope>/<id>/run',
   *  'report.pdf', { format: 'pdf', params })` posts the body when there is one, else GETs; the file takes the name
   *  the server gives it, else the one passed. Rejects with the server's message. Since version 6. */
  download: (path: string, name: string, body?: unknown) => Promise<void>
  /** hook: a function that says whether the caller holds a permission ('job:<id>', 'plugin:…'; a superuser
   *  holds them all) — to show a button only to who may use it. The server decides in the end. */
  useCan: () => (permission: string) => boolean
  /** hook: tell the assistant what the user is looking at, so a question about "this" needs no name:
   *  `useAssistantContext('Cross Reference', { object: 'P4210', kind: 'application', form: 'W4210A' })`.
   *  A few facts in a few words (blank ones are left out, long ones cut, 20 at most) — never rows. Sent with
   *  each question while this page is the one on screen; the chat shows it as a chip the user can drop. The
   *  application's starters for the page are `[ai.<app>.screens.<page>]` in ai.toml. Since version 4. */
  useAssistantContext: (title: string, facts?: Record<string, string | number | boolean | null | undefined>) => void
}

/** What `callKept` answers: the function's answer and when it was computed. */
export interface KeptAnswer<T = unknown> {
  data: T
  /** when it was computed (ISO date, UTC) — null when the server could not keep it */
  at: string | null
  /** who computed it: a user, or `job` */
  by: string | null
  /** how long the computing took, in milliseconds */
  ms: number | null
  /** true when this call computed it, false when it was already kept */
  fresh: boolean
}

export function buildSdk(app: string, page: string): PageSdk {
  return {
    version: PAGE_SDK_VERSION, app, page,
    React, styled, ui, api,
    Grid: PageGrid,
    Diagram,
    theme: { colors, fontSize, fonts, radius, shadow },
    icon: iconFor,
    call: async <T,>(fn: string, params: Record<string, unknown> = {}) => {
      const r = await api.post<{ extras?: unknown }>('/api/plugins/run', { callable: callableRef(app, fn), params })
      return (r.extras ?? r) as T
    },
    callKept: async <T,>(fn: string, params: Record<string, unknown> = {}, refresh = false) => {
      const r = await api.post<{ extras?: unknown; kept?: { at?: string; by?: string | null; ms?: number | null; fresh?: boolean } }>(
        '/api/plugins/run', { callable: callableRef(app, fn), params, kept: refresh ? 'refresh' : 'use' })
      return { data: (r.extras ?? r) as T, at: r.kept?.at ?? null, by: r.kept?.by ?? null, ms: r.kept?.ms ?? null, fresh: r.kept?.fresh ?? true }
    },
    useLang: () => useTranslation().i18n.language || 'en',
    useUser: () => {
      const { user } = useAuth()
      return user ? { username: user.username, is_superuser: !!user.is_superuser, permissions: user.permissions ?? [] } : null
    },
    download: (path: string, name: string, body?: unknown) => apiDownload(path, name, body === undefined ? {} : { json: body }),
    useOpen: () => {
      const navigate = useNavigate()
      return (path: string) => { void navigate(path) }
    },
    useCan: () => {
      const { user } = useAuth()
      return (permission: string) => can(user, permission)
    },
    useAssistantContext: (title, facts) => {
      const { pathname } = useLocation()
      // every open tab stays mounted: only the page on screen speaks, and it speaks again when the user comes back
      const onScreen = pathname === `/app/${app}/${page}`
      const said = JSON.stringify(pageFacts(facts))
      React.useEffect(() => {
        if (!onScreen) return
        setPageContext({ path: pathname, app, kind: 'page', title, connector: app, query: page, screen: page, facts: JSON.parse(said) as [string, string][] })
      }, [onScreen, pathname, title, said])
    },
  }
}
