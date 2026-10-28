// Retention in one place (C8): every store the server keeps growing — what it keeps, where that
// is set, how big it is — with Preview (a dry run: what would go, nothing deleted) and Run now.
import { useCallback, useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Eye, RefreshCw, Trash2 } from 'lucide-react'
import { ApiError } from '../../api/client'
import { Banner, Button, Centered, useModals } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { formatBytes } from '../../services/serverLog'
import { getRetention, policyText, runRetention, sizeText, type RunResult, type StoreStatus } from '../../services/retentionCenter'

const Bar = styled.div`display: flex; justify-content: flex-end; margin-bottom: 10px;`
const Table = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; overflow: hidden;`
const Row = styled.div`
  /* a fixed actions column: a row without buttons keeps the same column widths as the others */
  display: grid; grid-template-columns: minmax(180px, 1fr) minmax(220px, 1.4fr) minmax(160px, 1fr) 220px; gap: 14px; align-items: center;
  padding: 10px 14px; border-top: 1px solid ${colors.border}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:first-of-type { border-top: none; }
  & .name { color: ${colors.text.primary}; font-weight: 600; }
  & .muted { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  & .where { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .acts { display: flex; gap: 6px; justify-content: flex-end; }
  & .result { grid-column: 1 / -1; color: ${colors.text.primary}; }
  @media (max-width: 900px) { grid-template-columns: 1fr; }
`

export default function RetentionCenter() {
  const { t } = useTranslation()
  const modals = useModals()
  const [stores, setStores] = useState<StoreStatus[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, RunResult>>({})

  const load = useCallback(() => {
    getRetention().then((r) => { setStores(r.stores); setError(null) }).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  useEffect(load, [load])

  const go = async (s: StoreStatus, dryRun: boolean) => {
    if (!dryRun) {
      const ok = await modals.confirm({ title: t('retentionCenter.confirmTitle', { store: t(`retentionCenter.store.${s.id}`) }), message: policyText(s, t), variant: 'danger' })
      if (!ok) return
    }
    setBusy(s.id); setError(null)
    try {
      const r = await runRetention(s.id, dryRun)
      setResults((cur) => ({ ...cur, [s.id]: r }))
      if (!dryRun) load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(null) }
  }
  const resultText = (r: RunResult) => t(r.dry_run ? 'retentionCenter.wouldDelete' : 'retentionCenter.deleted', { n: r.deleted }) + (r.bytes ? ` (${formatBytes(r.bytes)})` : '')

  if (!stores && !error) return <Centered />
  return (
    <>
      <Bar><Button $variant="ghost" $size="sm" onClick={load}><RefreshCw size={14} /> {t('common.reload')}</Button></Bar>
      {error && <Banner $tone="error">{error}</Banner>}
      {stores && (
        <Table>
          {stores.map((s) => (
            <Row key={s.id}>
              <div>
                <div className="name">{t(`retentionCenter.store.${s.id}`)}</div>
                <div className="muted">{t(`retentionCenter.goes.${s.id}`)}</div>
              </div>
              <div>
                <div>{policyText(s, t)}</div>
                {s.where && <div className="where">{s.where}</div>}
              </div>
              <div>{sizeText(s, t)}</div>
              <div className="acts">
                {s.can_run && (
                  <>
                    <Button $variant="ghost" $size="sm" disabled={busy === s.id} onClick={() => void go(s, true)}><Eye size={13} /> {t('retentionCenter.preview')}</Button>
                    <Button $variant="danger" $size="sm" disabled={busy === s.id} onClick={() => void go(s, false)}><Trash2 size={13} /> {t('retentionCenter.runNow')}</Button>
                  </>
                )}
              </div>
              {results[s.id] && <div className="result">{resultText(results[s.id])}</div>}
            </Row>
          ))}
        </Table>
      )}
    </>
  )
}
