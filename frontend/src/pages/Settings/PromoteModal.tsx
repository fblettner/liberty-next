// "Promote to…" (C11): pick a target instance — the target previews the payload at once (a config
// package's plan, or a change package's drift report where conflicts / unverified ops can be
// forced) — then Apply there. Opened from Package → Build and from an approved change package.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Rocket } from 'lucide-react'
import { Banner, Button, Input, SearchSelect, SpinnerRing } from '../../common'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter } from '../../common/Modal'
import { colors, fontSize } from '../../theme'
import { ApiError } from '../../api/client'
import { getTargets, isPlan, push } from '../../services/promotion'
import { pendingChanges } from '../../services/packagePlan'
import type { PromotionPayload, PromotionTarget, PushResult } from '../../types/promotion'
import { PackagePlanView } from './PackagePlanView'
import { BundleReportView } from './ApplyBundleModal'

const STRATEGIES = ['overwrite', 'merge', 'replace_all'] as const

export function PromoteModal({ payload, onClose, onApplied }: {
  payload: PromotionPayload
  onClose: () => void
  onApplied?: () => void
}) {
  const { t } = useTranslation()
  const [targets, setTargets] = useState<Record<string, PromotionTarget> | null>(null)
  const [target, setTarget] = useState('')
  const [strategy, setStrategy] = useState<string>('overwrite')
  const [comment, setComment] = useState('')
  const [force, setForce] = useState<Set<number>>(new Set())
  const [preview, setPreview] = useState<PushResult | null>(null)
  const [result, setResult] = useState<PushResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getTargets()
      .then((ts) => { setTargets(ts); const names = Object.keys(ts); if (names.length === 1) setTarget(names[0]) })
      .catch((e) => { setTargets({}); setError(e instanceof ApiError ? e.message : String(e)) })
  }, [])

  // A target (or another strategy) picked → the target previews at once; nothing is written.
  useEffect(() => {
    if (!target) { setPreview(null); return }
    let cancelled = false
    setBusy('preview'); setError(null); setResult(null)
    push(target, payload, { apply: false, strategy })
      .then((r) => { if (!cancelled) { setPreview(r); setForce(new Set()) } })
      .catch((e) => { if (!cancelled) { setPreview(null); setError(e instanceof ApiError ? e.message : String(e)) } })
      .finally(() => { if (!cancelled) setBusy(null) })
    return () => { cancelled = true }
  }, [target, strategy, payload])

  async function apply() {
    setBusy('apply'); setError(null)
    try {
      const r = await push(target, payload, { apply: true, strategy, force: [...force], comment })
      setResult(r); setPreview(null)
      onApplied?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const toggleForce = (i: number) => setForce((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n })
  const targetLabel = (name: string) => targets?.[name]?.label || name
  const pending = preview ? (isPlan(preview.report) ? pendingChanges(preview.report) : preview.report.op_count) : 0
  const shown = result ?? preview
  const names = Object.keys(targets ?? {})

  return createPortal(
    <Overlay onClick={busy ? undefined : onClose}>
      <Modal style={{ width: 'min(980px, 94vw)', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader><Rocket size={16} style={{ verticalAlign: -3, marginRight: 8 }} />{t('settings.promotion.title')}
          <span style={{ color: colors.text.muted, fontWeight: 400, marginLeft: 8 }}>· {payload.kind === 'changes' ? payload.name : t('settings.promotion.payloadConfig')}</span>
        </ModalHeader>
        <ModalBody style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {targets && names.length === 0 && <Banner $tone="info">{t('settings.promotion.noTargets')}</Banner>}
          {names.length > 0 && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <label style={{ flex: '1 1 240px', display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm, color: colors.text.secondary }}>
                {t('settings.promotion.target')}
                <SearchSelect value={target} onChange={setTarget} placeholder={t('settings.promotion.pickTarget')}
                  options={names.map((n) => ({ value: n, label: targetLabel(n), mono: n }))} />
              </label>
              {payload.kind === 'config' && (
                <label style={{ flex: '0 1 220px', display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm, color: colors.text.secondary }}>
                  {t('settings.promotion.strategy')}
                  <SearchSelect value={strategy} onChange={(v) => setStrategy(v || 'overwrite')}
                    options={STRATEGIES.map((s) => ({ value: s, label: t(`settings.package.${s === 'replace_all' ? 'replaceAll' : s}`) }))} />
                </label>
              )}
              <label style={{ flex: '2 1 280px', display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm, color: colors.text.secondary }}>
                {t('settings.promotion.comment')}
                <Input value={comment} onChange={(e) => setComment(e.target.value)} placeholder={t('settings.promotion.commentPh')} />
              </label>
            </div>
          )}
          {error && <Banner $tone="error">{error}</Banner>}
          {busy === 'preview' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: colors.text.muted, fontSize: fontSize.sm }}>
              <SpinnerRing size={13} thickness={2} /> {t('settings.promotion.previewing', { target: targetLabel(target) })}
            </div>
          )}
          {result && <Banner $tone="ok">{t('settings.promotion.appliedOn', { target: result.target.label })}</Banner>}
          {preview && !result && busy !== 'preview' && (
            <Banner $tone="info">
              {pending === 0 ? t('settings.promotion.nothing')
                : t('settings.promotion.previewOf', { target: preview.target.label, instance: preview.target.instance ?? '?', version: preview.target.version ?? '?' })}
            </Banner>
          )}
          {shown && busy !== 'preview' && (isPlan(shown.report)
            ? <PackagePlanView plan={shown.report} />
            : <BundleReportView report={shown.report} force={force} onToggleForce={toggleForce} />)}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" onClick={onClose} disabled={busy === 'apply'}>{result ? t('common.close') : t('common.cancel')}</Button>
          {!result && (
            <Button $variant="primary" onClick={() => void apply()} disabled={!preview || pending === 0 || !!busy}>
              {busy === 'apply' ? <SpinnerRing size={13} thickness={2} /> : <Rocket size={13} />}
              {t('settings.promotion.applyOn', { target: target ? targetLabel(target) : '…' })}
            </Button>
          )}
        </ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}
