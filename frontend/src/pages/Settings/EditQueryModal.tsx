// Shared modal that opens a single query's editor from anywhere a query dropdown is rendered.
// Every place we ask the operator to *pick* a query (Screen Editor's read/update/insert/delete,
// the action `run_query` / `navigate` target, NestedFormTab read/update/insert, Menu item
// targets, Dictionary's Lookup / Sequence query field) used to leave the operator stuck if they
// realised the query needed tweaking — they had to leave the page, go to Settings → Connectors,
// find the connector + query, edit it, come back. This modal lets them edit the query in place.
//
// The modal:
//   1. Fetches ``/admin/config/connectors/parsed`` + ``/admin/config/schema``
//   2. Finds the matching query in the named connector
//   3. Renders the QueryDef SchemaForm (name / sql / writable / params / label / description)
//   4. On Save → PUT the whole connectors back + /admin/reload, then closes
//   5. On Cancel → discards local changes, closes
//
// NOT re-exported from common/index.ts — direct import keeps it in the Settings chunk.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Centered, Mono, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, Row,
  SchemaForm, SqlConnectorContext, SpinnerRing, useModals, type JsonSchema,
} from '../../common'
import type { ConfigSchemas, ConnectorsDoc } from '../../types/config'
import { flattenConnectorSections } from './connectorTables'
import { colors, fontSize, fonts } from '../../theme'

export interface EditQueryModalProps {
  connector: string
  queryName: string
  onClose: () => void
  /** Fired after a successful Save + reload — gives the caller a chance to refresh its own
   *  state (e.g. re-fetch its config doc so any rename of the query propagates).
   *  Optional — when omitted the modal just closes after Save. */
  onSaved?: () => void
  /** Optional seed for CREATE mode — when supplied + ``queryName`` doesn't yet exist on
   *  the connector, the modal initialises the form from this dict (plus ``name = queryName``)
   *  and Save APPENDS the new query to the connector. Used by the Add / Clone query buttons:
   *
   *    Add   → ``seed = {type: 'custom', sql: ''}``
   *    Clone → ``seed = deepCopy(<existing query>)``
   *
   *  Omitted → the modal stays in EDIT mode (current behaviour: load the existing query
   *  matching ``queryName`` and surface a "not found" banner if there's no match). */
  seed?: Record<string, unknown>
}

type Connectors = ConnectorsDoc['connectors']
type Query = Record<string, unknown>

export function EditQueryModal({ connector, queryName, onClose, onSaved, seed }: EditQueryModalProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const [conns, setConns] = useState<Connectors | null>(null)
  const [queryDefSchema, setQueryDefSchema] = useState<JsonSchema | null>(null)
  const [allDefs, setAllDefs] = useState<Record<string, JsonSchema>>({})
  const [originalJson, setOriginalJson] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    setError(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed'),
    ])
      .then(([s, d]) => {
        if (cancelled) return
        const sqlSchema = s.sql as JsonSchema | undefined
        const defs = (sqlSchema?.$defs ?? {}) as Record<string, JsonSchema>
        setQueryDefSchema((defs.QueryDef ?? null) as JsonSchema | null)
        setAllDefs(defs)
        // Flatten the sectioned connector shape into the flat `queries[]` the modal edits; the
        // PUT on save re-buckets it server-side (same contract as ConnectorsBuilder).
        const flatConns = flattenConnectorSections(d.connectors)
        let nextConns = flatConns
        // CREATE mode: seed provided + queryName isn't yet on the connector. Append a
        // fresh query entry built from ``seed`` so the SchemaForm renders against it; the
        // form's name field is editable so the operator can still rename before saving.
        const cur = (nextConns ?? {})[connector] ?? {}
        const arr = (Array.isArray(cur.queries) ? cur.queries : []) as Query[]
        const exists = arr.some((q) => q && q.name === queryName)
        if (seed && !exists) {
          const fresh = { ...seed, name: queryName }
          nextConns = { ...nextConns, [connector]: { ...cur, queries: [...arr, fresh] } }
        }
        setConns(nextConns)
        // Originals snapshot reflects the flattened on-disk state — when seeding a new query,
        // the dirty flag starts true (the seed itself is an unsaved insertion), so the Save
        // button is immediately active.
        setOriginalJson(JSON.stringify(flatConns))
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof ApiError ? e.message : String(e))
      })
    return () => { cancelled = true }
  }, [])

  // The matching query (case-sensitive — Connector queries are dict-keyed by exact name).
  // ``null`` when the query has been renamed since the modal opened (rare); ``undefined`` while
  // we're still loading.
  const conn = conns ? conns[connector] : undefined
  const queries = (conn && Array.isArray(conn.queries) ? conn.queries : []) as Query[]
  const queryIdx = queries.findIndex((q) => q && q.name === queryName)
  const query = queryIdx >= 0 ? queries[queryIdx] : null

  const dirty = conns != null && JSON.stringify(conns) !== originalJson

  const updateQuery = (next: Query) => {
    if (!conns) return
    const cur = conns[connector] ?? {}
    const arr = Array.isArray(cur.queries) ? (cur.queries as Query[]).slice() : []
    // Find the row we're editing. queryIdx (computed above) is the position when the
    // form first loaded; if the operator renamed the query, the name has drifted from
    // the original. Trust the index — the index is stable across renames within this
    // modal session because the array isn't reordered.
    if (queryIdx < 0) return
    arr[queryIdx] = next
    setConns({ ...conns, [connector]: { ...cur, queries: arr } })
  }

  const cancel = async () => {
    if (dirty) {
      const choice = await modals.choose({
        title: t('settings.editQuery.unsavedTitle', 'Unsaved changes'),
        message: t('settings.editQuery.unsavedMsg', 'Discard your edits to this query?'),
        options: [
          { value: 'discard', label: t('settings.screens.designer.discard', 'Discard'), variant: 'danger' },
          { value: 'keep', label: t('settings.screens.designer.keepEditing', 'Keep editing'), variant: 'ghost', autoFocus: true },
        ],
        cancelValue: 'keep',
      })
      if (choice !== 'discard') return
    }
    onClose()
  }

  const save = async () => {
    if (!conns) return
    setBusy(true); setError(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/connectors/parsed', { connectors: conns })
      await api.post<{ connectors: string[] }>('/admin/reload')
      onSaved?.()
      onClose()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const modalNode = (
    // Local Overlay (z-index 400) — sits ABOVE the parent modal (e.g. ScreenDesigner at 400 too,
    // but this one renders later in the body → DOM-order on-top). For prompts we open via
    // ``useModals`` from inside this modal, those use a higher TopOverlay (z-index 2000) so
    // they paint above us too — same convention every other modal in the app follows.
    <Overlay onClick={cancel}>
      <Modal style={{ width: 'min(820px, 95vw)', height: 'min(720px, 90vh)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
            <span>
              {seed
                ? t('settings.editQuery.titleNew', 'New query')
                : t('settings.editQuery.title', 'Edit query')} ·{' '}
              <Mono style={{ display: 'inline', color: colors.text.muted, fontSize: fontSize.sm, fontWeight: 400 }}>
                {connector}.{queryName}
              </Mono>
            </span>
            <Button $variant="ghost" $size="sm" onClick={cancel} disabled={busy}>
              <X size={13} /> {t('common.cancel')}
            </Button>
          </Row>
        </ModalHeader>
        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          {conns == null || queryDefSchema == null ? (
            <Centered />
          ) : query == null ? (
            <Banner $tone="info">
              {t('settings.editQuery.notFound', 'Query "{{q}}" not found on connector "{{c}}".', { q: queryName, c: connector })}
            </Banner>
          ) : (
            <SqlConnectorContext.Provider value={connector}>
              <SchemaForm
                schema={queryDefSchema}
                defs={allDefs}
                value={query}
                onChange={(v: Query) => updateQuery(v)}
              />
            </SqlConnectorContext.Provider>
          )}
        </ModalBody>
        <ModalFooter>
          <Row gap={8}>
            <Button $variant="ghost" $size="sm" onClick={cancel} disabled={busy}>
              <X size={13} /> {t('common.cancel')}
            </Button>
            <Button $variant="primary" $size="sm" disabled={busy || !dirty || !query} onClick={save}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : null} {t('common.save')}
            </Button>
          </Row>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
  return createPortal(modalNode, document.body)
}

// Suppress unused-import warnings for ``fonts`` — kept so future stylings can pull from the theme.
void fonts
