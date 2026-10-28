// The plan of a package against an instance (types/packagePlan.ts): counts per state, the
// warnings, then each file's entities — what changes first, identical ones on demand — and, on a
// changed entity, its fields before / after. Shown by Package → Import (the preview, then the
// result) and by "Promote to…" (the target's answer).
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Banner, Card, Checkbox, Tag, type Tone } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { STATE_ORDER, planCounts, shownEntities, splitKind } from '../../services/packagePlan'
import type { PackagePlan, PlanEntity, PlanState } from '../../types/packagePlan'

const TONE: Record<PlanState, Tone> = {
  new: 'green', changed: 'blue', removed: 'red', skipped: 'orange', kept: 'purple', identical: 'neutral',
}

const Wrap = styled.div`display: flex; flex-direction: column; gap: 10px;`
const Chips = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap;`
const Muted = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const FileCard = styled(Card)`padding: 0; overflow: hidden;`
const FileHead = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 9px 14px; background: ${colors.bg.input};
  border-bottom: 1px solid ${colors.border}; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
`
const Row = styled.button<{ $open: boolean }>`
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 7px 14px; border: none; text-align: left;
  background: ${({ $open }) => ($open ? 'var(--hover-subtle)' : 'transparent')}; cursor: pointer;
  border-bottom: 1px solid ${colors.border}; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:disabled { cursor: default; }
  &:hover:not(:disabled) { background: var(--hover-subtle); }
  & .chev { width: 14px; flex-shrink: 0; color: ${colors.text.muted}; display: inline-flex; }
  & .kind { color: ${colors.text.muted}; white-space: nowrap; }
  & .name { font-family: ${fonts.mono}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .detail { margin-left: auto; color: ${colors.text.muted}; text-align: right; }
`
const Diff = styled.div`
  display: grid; grid-template-columns: minmax(140px, 0.6fr) minmax(0, 1fr) minmax(0, 1fr); gap: 1px;
  background: ${colors.border}; border-bottom: 1px solid ${colors.border}; font-size: ${fontSize.sm};
  & > div { background: ${colors.bg.card}; padding: 5px 10px; min-width: 0; }
  & .h { color: ${colors.text.muted}; font-weight: 600; font-size: ${fontSize.micro}; text-transform: uppercase; letter-spacing: 0.04em; }
  & .k { font-family: ${fonts.mono}; color: ${colors.text.secondary}; overflow-wrap: anywhere; }
  & .v { font-family: ${fonts.mono}; white-space: pre-wrap; overflow-wrap: anywhere; }
  & .from { background: ${colors.red.bg}; color: ${colors.text.primary}; }
  & .to { background: ${colors.green.bg}; color: ${colors.text.primary}; }
  & .none { color: ${colors.text.muted}; font-style: italic; }
`
const ErrBox = styled.div`padding: 8px 14px; color: ${colors.red.main}; font-size: ${fontSize.sm}; border-bottom: 1px solid ${colors.border}; border-radius: ${radius.sm};`

export function PackagePlanView({ plan }: { plan: PackagePlan }) {
  const { t } = useTranslation()
  const [showIdentical, setShowIdentical] = useState(false)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const counts = planCounts(plan)
  const toggle = (key: string) => setOpen((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  return (
    <Wrap>
      <Chips>
        {STATE_ORDER.filter((s) => counts[s] > 0).map((s) => (
          <Tag key={s} $tone={TONE[s]}>{t(`settings.package.plan.state.${s}`)} {counts[s]}</Tag>
        ))}
        {counts.errors > 0 && <Tag $tone="red">{t('settings.package.plan.state.errors')} {counts.errors}</Tag>}
        {plan.source?.install && (
          <Muted>{t('settings.package.plan.from', { install: plan.source.install, version: plan.source.version ?? '?' })}</Muted>
        )}
        {counts.identical > 0 && (
          <span style={{ marginLeft: 'auto' }}>
            <Checkbox checked={showIdentical} onChange={setShowIdentical} label={t('settings.package.plan.showIdentical', { n: counts.identical })} />
          </span>
        )}
      </Chips>
      {plan.warnings.length > 0 && (
        <Banner $tone="warning">
          <ul style={{ margin: 0, paddingLeft: 18 }}>{plan.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </Banner>
      )}
      {plan.files.map((f) => {
        const list = shownEntities(f.entities, showIdentical)
        if (list.length === 0 && f.errors.length === 0) return null
        return (
          <FileCard key={f.file}>
            <FileHead>{f.file}<Muted>· {list.length}</Muted></FileHead>
            {f.errors.map((e, i) => <ErrBox key={i}>{e}</ErrBox>)}
            {list.map((e) => <EntityRow key={`${e.kind}:${e.name}`} e={e} file={f.file} open={open} toggle={toggle} />)}
          </FileCard>
        )
      })}
    </Wrap>
  )
}

function EntityRow({ e, file, open, toggle }: { e: PlanEntity; file: string; open: Set<string>; toggle: (k: string) => void }) {
  const { t } = useTranslation()
  const key = `${file}:${e.kind}:${e.name}`
  const hasDiff = (e.diff?.length ?? 0) > 0
  const isOpen = hasDiff && open.has(key)
  const { kind, scope } = splitKind(e.kind)
  const none = <span className="none">{t('settings.package.plan.none')}</span>
  return (
    <>
      <Row type="button" $open={isOpen} disabled={!hasDiff} onClick={() => toggle(key)}>
        <span className="chev">{hasDiff ? (isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : null}</span>
        <Tag $tone={TONE[e.state]}>{t(`settings.package.plan.state.${e.state}`)}</Tag>
        <span className="kind">{kind}{scope ? ` · ${scope}` : ''}</span>
        <span className="name">{e.name}</span>
        {e.detail && <span className="detail">{e.detail}</span>}
      </Row>
      {isOpen && (
        <Diff>
          <div className="h">{t('settings.package.plan.field')}</div>
          <div className="h">{t('settings.package.plan.before')}</div>
          <div className="h">{t('settings.package.plan.after')}</div>
          {e.diff!.map((d) => (
            <div key={d.key} style={{ display: 'contents' }}>
              <div className="k">{d.key}</div>
              <div className="v from">{d.from ?? none}</div>
              <div className="v to">{d.to ?? none}</div>
            </div>
          ))}
        </Diff>
      )}
    </>
  )
}
