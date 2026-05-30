// Clone-with-dependencies modal for screens / charts / dashboards.
//
// Asks the operator for the new id + an optional "Also clone referenced queries" flag,
// then POSTs to /admin/clone-with-deps. The backend walks the dependency closure,
// duplicates the chosen subset under suffixed names (``<original>_<suffix>``), rewrites
// the cloned entity's refs to point at the new names, and writes everything atomically.
//
// Use case: customer wants to fork ``nomasx1.security_users`` for their customisation.
// Without "clone queries", the clone is a different screen wired to the same base
// CRUD queries — editing the clone's column hints still touches the base behaviour.
// With "clone queries", the clone is fully isolated — every CRUD button on the clone
// is wired to a NEW query (``<base>_get_custom`` etc.), free to edit without leaking
// into the base.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, AlertCircle } from 'lucide-react'
import { api, authHeaders } from '../../api/client'
import { Banner, Button, Checkbox, Field, Input, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SpinnerRing, Mono } from '../../common'
import { validateId, suggestCloneId } from '../../services/idValidator'
import { colors, fontSize, fonts } from '../../theme'

export interface CloneWithDepsModalProps {
  /** What's being cloned — picks the right id-kind for validation + the right
   *  /admin/clone-with-deps payload. */
  kind: 'screen' | 'chart' | 'dashboard'
  /** Source entity's name. */
  name: string
  /** Source entity's scope (app for screen, connector for chart, scope for dashboard).
   *  Required for screen + chart; dashboards may have a null scope (top-level). */
  scope: string | null
  /** Existing ids under the same scope — drives the validator's duplicate check + the
   *  default name suggestion (``<name>_copy`` / ``_copy2``…). */
  existingNames: string[]
  /** Fires after a successful clone (so the parent can refresh its config doc + jump
   *  to the new entity). The new id is passed back. */
  onCloned: (newName: string) => void
  /** Fires on Cancel / overlay click. */
  onClose: () => void
}

interface ClonePreviewItem {
  kind: string
  scope: string | null
  new_name: string
  original: { kind: string; scope: string | null; name: string }
}

interface CloneResponse {
  cloned: boolean
  items: ClonePreviewItem[]
  warnings: string[]
  summary: string
}

const TITLE_BY_KIND: Record<string, string> = {
  screen: 'Clone screen',
  chart: 'Clone chart',
  dashboard: 'Clone dashboard',
}

export function CloneWithDepsModal({ kind, name, scope, existingNames, onCloned, onClose }: CloneWithDepsModalProps) {
  const { t } = useTranslation()
  const [newName, setNewName] = useState(() => suggestCloneId(name, existingNames))
  const [cloneQueries, setCloneQueries] = useState(false)
  // Mark the clone as customer override → vendor upgrade-package imports leave it
  // alone (the operator's fork is protected). Default ON because clone-for-fork is the
  // dominant flow; operator can untick for an A/B test or temporary copy that should
  // follow vendor updates.
  const [markOverride, setMarkOverride] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Live preview — when "Also clone referenced queries" is ticked, ask the backend
  // to walk the dependency closure so the operator sees what'll happen BEFORE
  // they confirm. Lightweight: same /admin/find-dependencies endpoint the Package
  // & Deploy tab uses, scoped to this single seed + filtered to queries.
  const [previewCount, setPreviewCount] = useState<number | null>(null)
  useEffect(() => {
    if (!cloneQueries) { setPreviewCount(null); return }
    let cancelled = false
    api.post<{ counts: Record<string, number> }>('/admin/find-dependencies', {
      seeds: [{ kind, name, scope }],
    })
      .then((r) => { if (!cancelled) setPreviewCount(r.counts.query ?? 0) })
      .catch(() => { if (!cancelled) setPreviewCount(null) })
    return () => { cancelled = true }
  }, [cloneQueries, kind, name, scope])

  const validation = validateId({
    kind: kind === 'screen' ? 'screen' : kind === 'chart' ? 'chart' : 'dashboard',
    proposed: newName,
    existing: existingNames,
    mode: 'clone',
  })
  const blockedByValidation = !!validation.error

  // Suffix derivation — the bit of the new name that differs from the source. Used by
  // the backend to derive each cloned query's new name as ``<original>_<suffix>``.
  // ``security_users`` → ``security_users_custom`` produces suffix ``custom``;
  // ``security_users_v2`` → ``security_users_alt`` falls back to suffix ``alt`` (when
  // the new name doesn't share the source's prefix — backend handles both shapes).
  const suffix = newName.startsWith(name + '_') ? newName.slice(name.length + 1) : newName

  async function submit() {
    if (blockedByValidation) return
    setBusy(true); setError(null)
    try {
      const body = { kind, name, scope, new_name: newName, suffix,
        options: { clone_queries: cloneQueries, mark_override: markOverride } }
      const res = await fetch('/admin/clone-with-deps', {
        method: 'POST',
        headers: { ...authHeaders({ 'Content-Type': 'application/json' }) },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({} as Record<string, unknown>))
        throw new Error((body && typeof body === 'object' && 'detail' in body) ? String((body as { detail: unknown }).detail) : `clone failed: ${res.status}`)
      }
      const data = await res.json() as CloneResponse
      onCloned(newName)
      if (data.warnings.length > 0) {
        // Non-fatal — the clone succeeded but the backend flagged something the
        // operator should know about (e.g. dangling refs that the rewriter left
        // pointing at the originals). Surface but let the parent close anyway.
        setError(t('settings.cloneDeps.partialWarnings',
          'Clone succeeded with warnings: {{w}}', { w: data.warnings.join('; ') }))
        return
      }
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Overlay onClick={onClose}>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(560px, 96vw)' }}>
        <ModalHeader>{t(`settings.cloneDeps.${kind}.title`, TITLE_BY_KIND[kind])}</ModalHeader>
        <ModalBody>
          <Field label={t('settings.cloneDeps.newName', 'New id')}>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !blockedByValidation) { e.preventDefault(); void submit() } }}
              autoFocus
              placeholder="snake_case"
            />
            {validation.error && <div style={{ color: colors.red.main, fontSize: fontSize.sm, marginTop: 4 }}>{validation.error}</div>}
            {!validation.error && validation.warning && (
              <div style={{ color: colors.orange.main, fontSize: fontSize.sm, marginTop: 4 }}>⚠ {validation.warning}</div>
            )}
          </Field>

          <div style={{ marginTop: 14 }}>
            <Checkbox
              checked={markOverride}
              onChange={setMarkOverride}
              label={t('settings.cloneDeps.markOverride', 'Mark the clone as customer override (preserved by future vendor upgrades)')}
            />
            <div style={{ color: colors.text.muted, fontSize: fontSize.sm, marginTop: 4, paddingLeft: 24 }}>
              {markOverride
                ? t('settings.cloneDeps.markOverrideOn',
                    'A subsequent upgrade-package import with the overwrite strategy will leave this clone alone.')
                : t('settings.cloneDeps.markOverrideOff',
                    'The clone will follow vendor updates (an upgrade may overwrite your edits). Tick the box if this is your customer fork.')}
            </div>
          </div>

          <div style={{ marginTop: 14 }}>
            <Checkbox
              checked={cloneQueries}
              onChange={setCloneQueries}
              label={t('settings.cloneDeps.cloneQueries', 'Also clone referenced queries')}
            />
            <div style={{ color: colors.text.muted, fontSize: fontSize.sm, marginTop: 4, paddingLeft: 24 }}>
              {cloneQueries ? (
                previewCount === null
                  ? t('settings.cloneDeps.previewLoading', 'Computing closure…')
                  : previewCount === 0
                    ? t('settings.cloneDeps.previewEmpty', 'This entity references no queries.')
                    : t('settings.cloneDeps.previewCount',
                        '{{n}} referenced quer(y/ies) will be duplicated as <name>_{{suffix}}',
                        { n: previewCount, suffix: suffix || 'copy' })
              ) : (
                t('settings.cloneDeps.unticked',
                  'The clone will reference the SAME queries as the original — editing the clone\'s SQL would affect both. Tick the box to create independent copies.')
              )}
            </div>
          </div>

          <div style={{ marginTop: 14, fontSize: fontSize.sm, color: colors.text.muted, fontFamily: fonts.sans }}>
            {t('settings.cloneDeps.source', 'Source:')} <Mono>{scope ? `${scope}.${name}` : name}</Mono>
          </div>

          {error && (
            <Banner $tone="error" style={{ marginTop: 12 }}>
              <AlertCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              {error}
            </Banner>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button $size="sm" $variant="primary" onClick={() => void submit()} disabled={busy || blockedByValidation}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Copy size={13} />}
            {t('common.clone', 'Clone')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export default CloneWithDepsModal
