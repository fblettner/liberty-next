// Visual chart editor — a modal with two tabs, opened from the Charts settings list:
//   • General — id / label / description / connector / query (the chart's metadata).
//   • Chart   — the SAME visual builder the TableView Chart tab uses (ChartSpecEditor: type /
//     X / series / aggregate) with a live ChartCanvas underneath, driven by a sample of the
//     chosen query. Pick columns by dropdown, watch the chart take shape.
// Cancel discards; Save validates + hands the record back to ChartsBuilder (which persists it).
// Same shape as the screen visual dialog — a focused modal, not an inline form.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { SlidersHorizontal, BarChart3 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Field, Input, SearchSelect, SpinnerRing, useModals, type SearchSelectOption } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { ChartSpecEditor } from '../TableView/ChartSpecEditor'
import { ChartCanvas } from '../TableView/ChartCanvas'
import type { QueryResult } from '../../types/connectors'
import type { ChartSpec, SavedChartSpec } from '../../types/charts'
import { defaultChartSpec, fromSavedSpec, toSavedSpec } from '../../types/charts'
import { EditQueryButton, CloneQueryButton, AddQueryButton } from './EditQueryButton'
import { colors, fontSize, fonts } from '../../theme'

const PREVIEW_LIMIT = 1000

export interface ChartRecord {
  id: string
  label?: string
  description?: string | null
  connector?: string
  query?: string
  spec?: SavedChartSpec
}

const Tabs = styled.div`display: flex; gap: 16px; padding: 0 18px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;`
const TabBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 7px; padding: 11px 4px; margin-bottom: -1px;
  border: none; background: transparent; cursor: pointer; font-size: ${fontSize.md}; font-weight: 600; font-family: ${fonts.sans};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom: 2px solid ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  &:hover { color: ${colors.text.primary}; }
`
const Grid2 = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 12px;`
const PreviewBox = styled.div`
  flex: 1; min-height: 240px; display: flex; flex-direction: column; margin-top: 4px;
`
const Msg = styled.div`
  flex: 1; display: flex; align-items: center; justify-content: center; text-align: center; padding: 16px;
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
`
const slug = (s: string) => s.trim()

export function ChartEditorModal({
  initial, scope, takenIds, onSave, onClose,
}: {
  initial: ChartRecord | null
  /** The connector scope this chart belongs to — charts are stored at `[charts.<scope>.<id>]`,
   *  so the connector is fixed by the scope (not editable here). */
  scope: string
  takenIds: string[]            // ids already used by OTHER charts in this scope (uniqueness check)
  onSave: (id: string, record: Record<string, unknown>) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const modals = useModals()
  const { connectors } = useWorkspace()
  const isNew = initial == null
  const connector = scope                 // the chart's connector IS its scope

  const [tab, setTab] = useState<'general' | 'chart'>('general')
  const [id, setId] = useState(initial?.id ?? '')
  const [label, setLabel] = useState(initial?.label ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [query, setQuery] = useState(initial?.query ?? '')
  const [spec, setSpec] = useState<ChartSpec>(initial?.spec ? fromSavedSpec(initial.spec) : defaultChartSpec())
  const [error, setError] = useState<string | null>(null)

  // Unsaved-changes guard — same Save / Discard / Keep-editing dialog the screen visual dialog
  // uses. Snapshot the editable state on first render; compare to it on every close attempt.
  const draftKey = JSON.stringify({ id, label, description, query, spec })
  const initialKey = useRef<string | null>(null)
  if (initialKey.current === null) initialKey.current = draftKey
  const dirty = draftKey !== initialKey.current

  // Sample for the builder + preview — refetched when connector/query change.
  const [sample, setSample] = useState<QueryResult | null>(null)
  const [sampleErr, setSampleErr] = useState<string | null>(null)
  useEffect(() => {
    setSample(null); setSampleErr(null)
    if (!connector || !query) return
    let cancelled = false
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}?_limit=${PREVIEW_LIMIT}`)
      .then((r) => { if (!cancelled) setSample(r) })
      .catch((e) => { if (!cancelled) setSampleErr(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [connector, query])

  const sqlConnectors = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql'), [connectors])
  const queryOpts: SearchSelectOption[] = useMemo(() => {
    const c = sqlConnectors.find((x) => x.name === connector)
    return (c?.queries ?? []).map((q) => ({ value: q.name, label: q.description || q.label || q.name, mono: q.name }))
  }, [sqlConnectors, connector])

  const save = () => {
    const cid = slug(id)
    if (!cid) { setError(t('settings.charts.errId', 'An id is required.')); setTab('general'); return }
    if (!/^[A-Za-z0-9_-]+$/.test(cid)) { setError(t('settings.charts.errIdShape', 'Id: letters, digits, underscore, hyphen only.')); setTab('general'); return }
    if (takenIds.includes(cid)) { setError(t('settings.charts.errIdTaken', 'That id is already used by another chart.')); setTab('general'); return }
    if (!query) { setError(t('settings.charts.errQuery', 'Pick a query.')); setTab('general'); return }
    if (!spec.x || spec.y.length === 0) { setError(t('settings.charts.errXY', 'Set the X column and at least one Y series.')); setTab('chart'); return }
    // `connector` is the scope (the dict path key) — the builder places the record under it, so we
    // don't write it into the body.
    const record: Record<string, unknown> = {
      id: cid,
      label: label.trim() || cid,
      query,
      spec: toSavedSpec(spec),
    }
    if (description.trim()) record.description = description.trim()
    onSave(cid, record)
  }

  // Guarded close — discard / save / keep editing when there are unsaved edits.
  const requestClose = async () => {
    if (!dirty) { onClose(); return }
    const choice = await modals.choose<'discard' | 'save' | 'keep'>({
      title: t('settings.screens.designer.unsavedTitle', 'Unsaved changes'),
      message: t('settings.screens.designer.unsavedMsg', 'You have unsaved changes. Save them, discard them, or keep editing?'),
      options: [
        { value: 'discard', label: t('settings.screens.designer.discard', 'Discard'), variant: 'danger' },
        { value: 'save', label: t('common.save'), variant: 'primary' },
        { value: 'keep', label: t('settings.screens.designer.keepEditing', 'Keep editing'), variant: 'ghost', autoFocus: true },
      ],
      cancelValue: 'keep',
    })
    if (choice === 'save') save()        // save() validates; on invalid it shows an error + stays open
    else if (choice === 'discard') onClose()
    // keep / null → stay
  }

  return (
    <EditorModalShell
      title={(
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontFamily: fonts.mono }}>
          <BarChart3 size={16} color={colors.blue.main} />
          {isNew ? t('settings.charts.add', 'Add chart') : `[charts.${scope}.${initial?.id}]`}
        </span>
      )}
      onClose={() => void requestClose()}
      onSave={save}
      dirty={dirty}
      frameStyle={{ width: 'min(960px, 96vw)', height: 'min(680px, 92vh)' }}
      tabs={(
        <Tabs>
          <TabBtn $active={tab === 'general'} onClick={() => setTab('general')}><SlidersHorizontal size={14} /> {t('settings.charts.tabGeneral', 'General')}</TabBtn>
          <TabBtn $active={tab === 'chart'} onClick={() => setTab('chart')}><BarChart3 size={14} /> {t('settings.charts.tabChart', 'Chart')}</TabBtn>
        </Tabs>
      )}
    >
      {error && <Banner $tone="error">{error}</Banner>}
          {tab === 'general' ? (
            <>
              <Grid2>
                <Field label={t('settings.charts.id', 'Id')}>
                  <Input value={id} onChange={(e) => { setId(e.target.value); setError(null) }} placeholder="users_per_app" />
                </Field>
                <Field label={t('settings.charts.label', 'Label')}>
                  <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t('settings.charts.label', 'Label')} />
                </Field>
              </Grid2>
              <Field label={t('settings.charts.description', 'Description')}>
                <Input value={description} onChange={(e) => setDescription(e.target.value)} />
              </Field>
              <Grid2>
                <Field label={t('settings.charts.connector', 'Connector')}>
                  {/* The connector is the chart's scope — fixed here. Move a chart between
                      connectors by deleting + re-adding it under the other scope. */}
                  <Input value={scope} readOnly disabled style={{ fontFamily: fonts.mono }} />
                </Field>
                <Field label={t('settings.charts.query', 'Query')}>
                  {/* Edit / Clone / Add trio sits next to the picker so the operator can
                      fix the SQL inline, fork it for the customer (Clone), or scaffold a
                      brand-new query (Add) — without bouncing to Settings → Connectors.
                      Shared icons + EditQueryModal: same UX as the dictionary lookup /
                      sequence + screen-editor surfaces. */}
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <SearchSelect value={query} onChange={(v) => { setQuery(v); setError(null) }}
                        options={queryOpts} placeholder={t('chart.spec.pick', 'Pick…')} allowCustom />
                    </div>
                    <EditQueryButton connector={connector} queryName={query} />
                    <CloneQueryButton connector={connector} queryName={query}
                      existingNames={queryOpts.map((o) => o.value)} />
                    <AddQueryButton connector={connector}
                      existingNames={queryOpts.map((o) => o.value)} />
                  </div>
                </Field>
              </Grid2>
            </>
          ) : (
            <PreviewBox>
              {!connector || !query ? (
                <Msg>{t('settings.charts.previewNeedQuery', 'Set a connector + query on the General tab first.')}</Msg>
              ) : sampleErr ? (
                <Msg style={{ color: colors.red.main }}>{sampleErr}</Msg>
              ) : !sample ? (
                <Msg><SpinnerRing size={20} thickness={2} /></Msg>
              ) : (
                <>
                  <ChartSpecEditor result={sample} spec={spec} onChange={setSpec} />
                  <div style={{ flex: 1, minHeight: 220, marginTop: 10, display: 'flex' }}>
                    <ChartCanvas result={sample} spec={spec} connector={connector}
                      emptyMessage={t('settings.charts.previewSetXY', 'Set the X column + at least one Y series to preview.')} />
                  </div>
                </>
              )}
            </PreviewBox>
          )}
    </EditorModalShell>
  )
}

export default ChartEditorModal
