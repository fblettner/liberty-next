// "Save chart" modal — lifts the in-session ChartSpec into config/charts.toml via
// /admin/config/charts/parsed. The operator picks an id (slug) and a display label;
// description is optional. The chart's connector + query are read from the TableView's
// current screen, the spec from the user's current chart settings.
//
// Workflow:
//   1. GET /admin/config/charts/parsed (so we don't blow away other operators' saved charts)
//   2. Merge the new chart in (an existing id is rejected with a friendly message)
//   3. PUT the merged dict → backend rewrites only the [charts] table via tomlkit
//   4. POST /admin/reload so the runtime picks it up (the new chart shows in GET /api/charts)
//   5. Close + onSaved() so the parent can refresh anything that lists charts
//
// Superuser only — the underlying admin endpoints return 403 otherwise; the button shouldn't
// render for non-superusers (callers gate it via the principal's is_superuser flag).
import { useState } from 'react'
import styled from '@emotion/styled'
import { Save, AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner } from '../../common/Banner'
import { Button } from '../../common/Button'
import { Field, Input, Textarea } from '../../common/Input'
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from '../../common/Modal'
import { SpinnerRing } from '../../common/Spinner'
import type { ChartConfig, ChartSpec, SavedChartSpec } from '../../types/charts'
import { toSavedSpec } from '../../types/charts'
import { colors, fontSize } from '../../theme'

const Hint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const FieldError = styled.div`color: ${colors.red.main}; font-size: ${fontSize.micro}; margin-top: 4px;`

// Nested shape: scope (connector) → id → chart body (id + connector are path-derived, not in body).
interface ChartsParsedResponse { path: string; charts: Record<string, Record<string, Omit<ChartConfig, 'id' | 'connector'>>> }

const ID_RE = /^[a-z][a-z0-9_]*$/  // slug-style — same shape the other config sections use

export interface SaveChartModalProps {
  connector: string
  query: string
  spec: ChartSpec
  /** Default values pre-filled into the form. `id` is left blank — operator picks it. */
  defaultLabel?: string
  defaultDescription?: string
  onSaved: (id: string) => void
  onCancel: () => void
}

export function SaveChartModal({
  connector, query, spec, defaultLabel = '', defaultDescription = '', onSaved, onCancel,
}: SaveChartModalProps) {
  const { t } = useTranslation()
  const [id, setId] = useState('')
  const [label, setLabel] = useState(defaultLabel)
  const [description, setDescription] = useState(defaultDescription)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Inline validation — surfaced under the field, prevents the Save button from firing.
  const idTrimmed = id.trim()
  const idError = !idTrimmed ? null
    : !ID_RE.test(idTrimmed) ? t('chart.save.idShape')
    : null
  const labelTrimmed = label.trim()
  const canSubmit = !!idTrimmed && !idError && !!labelTrimmed && !busy

  async function save() {
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      // Load the current file, merge in the new chart under [charts.<connector>.<id>] so we
      // don't trample anything else.
      const current = await api.get<ChartsParsedResponse>('/admin/config/charts/parsed')
      const scopeCharts = current.charts[connector] ?? {}
      if (idTrimmed in scopeCharts) {
        setError(t('chart.save.idExists', { id: idTrimmed }))
        setBusy(false)
        return
      }
      const savedSpec: SavedChartSpec = toSavedSpec(spec)
      const next = {
        ...current.charts,
        [connector]: {
          ...scopeCharts,
          [idTrimmed]: {
            label: labelTrimmed,
            ...(description.trim() ? { description: description.trim() } : {}),
            query,
            spec: savedSpec,
          },
        },
      }
      await api.put('/admin/config/charts/parsed', { charts: next })
      // Hot-reload so the new chart appears in GET /api/charts immediately.
      await api.post('/admin/reload')
      onSaved(idTrimmed)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <Overlay onClick={onCancel}>
      <Modal style={{ width: 'min(520px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('chart.save.title')}</ModalHeader>
        <ModalBody>
          <Hint>{t('chart.save.hint', { connector, query })}</Hint>
          <div>
            <Field label={t('chart.save.id') + ' *'}>
              <Input
                value={id} onChange={(e) => setId(e.target.value)}
                placeholder="e.g. users_per_app" autoFocus
              />
            </Field>
            {idError && <FieldError>{idError}</FieldError>}
          </div>
          <Field label={t('chart.save.label') + ' *'}>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t('chart.save.labelHint')} />
          </Field>
          <Field label={t('chart.save.description')}>
            <Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('chart.save.descriptionHint')} />
          </Field>
          {error && <Banner $tone="error"><AlertTriangle size={14} /> {error}</Banner>}
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel} disabled={busy}>{t('common.cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={save} disabled={!canSubmit}>
            {busy ? <SpinnerRing size={12} thickness={2} /> : <Save size={12} />} {t('chart.save.commit')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
