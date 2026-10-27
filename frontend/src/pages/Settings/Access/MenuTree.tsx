// The role editor's menu tree: one block per app, the same nodes the sidebar shows, with a
// three-state control per node (inherit / allow / deny). A folder's state cascades; a leaf
// shows what it runs and whether the sidebar will show it for a holder of the whole rule set
// (globs from "other access" included), so the tree is also the preview.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Ban, Check, ChevronDown, ChevronRight, Eye, EyeOff, Minus } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../../../theme'
import type { MenuForest, MenuNode, NodeEffect, NodeState } from './permissions'
import { isLeaf } from './permissions'

const AppBlock = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; overflow: hidden;
  & + & { margin-top: 8px; }
`
const AppHead = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 7px 10px; background: ${colors.bg.card};
  border-bottom: 1px solid ${colors.border}; font-size: ${fontSize.sm}; font-weight: 700; color: ${colors.text.primary};
  & .count { font-weight: 500; color: ${colors.text.muted}; }
`
const Row = styled.div<{ $depth: number; $eff: NodeState | null }>`
  display: flex; align-items: center; gap: 8px; min-height: 30px; padding: 2px 8px 2px ${({ $depth }) => 8 + $depth * 18}px;
  border-bottom: 1px solid ${colors.border};
  background: ${({ $eff }) => ($eff === 'deny' ? colors.red.bg : $eff === 'allow' ? colors.green.bg : 'transparent')};
  &:last-child { border-bottom: 0; }
  & .chev { width: 16px; display: inline-flex; color: ${colors.text.muted}; flex-shrink: 0; border: 0; background: transparent; padding: 0; cursor: pointer; }
  & .label { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 8px; overflow: hidden; }
  & .name { font-size: ${fontSize.base}; color: ${colors.text.primary}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  & .runs { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  & .inh { font-size: ${fontSize.micro}; color: ${colors.text.muted}; white-space: nowrap; }
  & .eye { display: inline-flex; width: 18px; justify-content: center; flex-shrink: 0; }
`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.sm}; overflow: hidden; flex-shrink: 0; background: ${colors.bg.card};`
const SegBtn = styled.button<{ $active?: boolean; $tone?: 'allow' | 'deny' | 'none' }>`
  display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 22px; border: 0; cursor: pointer;
  background: ${({ $active, $tone }) => (!$active ? 'transparent' : $tone === 'deny' ? colors.red.bg : $tone === 'allow' ? colors.green.bg : colors.blue.bg)};
  color: ${({ $active, $tone }) => (!$active ? colors.text.muted : $tone === 'deny' ? colors.red.main : $tone === 'allow' ? colors.green.main : colors.blue.main)};
  & + & { border-left: 1px solid ${colors.border}; }
  &:hover { color: ${colors.text.primary}; }
`

interface Props {
  forest: MenuForest
  effects: Record<string, NodeEffect>
  onSetState: (key: string, state: NodeState | null) => void
}

export default function MenuTree({ forest, effects, onSetState }: Props) {
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const toggle = (key: string) => setCollapsed((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n })

  const word = (s: NodeState | null) => (s === 'allow' ? t('settings.access.allowed', 'allowed') : s === 'deny' ? t('settings.access.denied', 'denied') : t('settings.access.none', 'no access'))

  const renderNode = (n: MenuNode, depth: number): React.ReactNode => {
    const e = effects[n.key] ?? { own: null, inherited: null, visible: null }
    const eff = e.own ?? e.inherited
    const leaf = isLeaf(n)
    const open = !collapsed.has(n.key)
    const runs = n.type === 'page' ? t('settings.access.page', 'page') : n.targetPerm ?? (n.type ? `${n.type} · ${n.target ?? ''}` : '')
    return (
      <div key={n.key}>
        <Row $depth={depth} $eff={eff}>
          {!leaf && n.children.length > 0
            ? <button type="button" className="chev" onClick={() => toggle(n.key)} title={open ? t('settings.access.collapse', 'Collapse') : t('settings.access.expand', 'Expand')}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
            : <span className="chev" />}
          <span className="label">
            <span className="name" title={n.id}>{n.label}</span>
            {runs && <span className="runs" title={runs}>{runs}</span>}
          </span>
          {e.own == null && <span className="inh">{t('settings.access.inherits', 'inherits')}: {word(e.inherited)}</span>}
          <span className="eye" title={leaf ? (e.visible ? t('settings.access.shown', 'Shown in the sidebar') : t('settings.access.hidden', 'Hidden')) : ''}>
            {leaf ? (e.visible ? <Eye size={13} color={colors.green.main} /> : <EyeOff size={13} color={colors.text.muted} />) : null}
          </span>
          <Seg>
            <SegBtn $active={e.own == null} $tone="none" title={t('settings.access.inherit', 'Inherit')} onClick={() => onSetState(n.key, null)}><Minus size={12} /></SegBtn>
            <SegBtn $active={e.own === 'allow'} $tone="allow" title={t('settings.access.allow', 'Allow')} onClick={() => onSetState(n.key, 'allow')}><Check size={12} /></SegBtn>
            <SegBtn $active={e.own === 'deny'} $tone="deny" title={t('settings.access.deny', 'Deny')} onClick={() => onSetState(n.key, 'deny')}><Ban size={12} /></SegBtn>
          </Seg>
        </Row>
        {open && n.children.map((c) => renderNode(c, depth + 1))}
      </div>
    )
  }

  const apps = Object.entries(forest)
  if (apps.length === 0) return <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.access.noMenus', 'No menus defined yet.')}</span>
  return (
    <div>
      {apps.map(([app, roots]) => {
        const leaves = roots.flatMap(function count(n: MenuNode): MenuNode[] { return isLeaf(n) ? [n] : n.children.flatMap(count) })
        const shown = leaves.filter((l) => effects[l.key]?.visible).length
        return (
          <AppBlock key={app}>
            <AppHead>{app} <span className="count">· {t('settings.access.shownCount', '{{shown}} of {{total}} shown', { shown, total: leaves.length })}</span></AppHead>
            {roots.map((r) => renderNode(r, 0))}
          </AppBlock>
        )
      })}
    </div>
  )
}
