// The role editor's menu tree: one block per app, the same nodes the sidebar shows, with one
// control per node — follows its folder / read / read and change / hidden. A folder's choice
// goes down to what it holds; an entry says how much the choice gives (the server works that
// out) and whether the sidebar will show it for a holder of the whole rule set (globs from
// "other access" included), so the tree is also the preview.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Ban, BookOpen, ChevronDown, ChevronRight, Eye, EyeOff, Minus, Pencil } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../../../theme'
import type { MenuForest, MenuNode, NodeEffect, NodeState } from './permissions'
import { appKey, isLeaf } from './permissions'

const AppBlock = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; overflow: hidden;
  & + & { margin-top: 8px; }
`
const AppHead = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 7px 10px; background: ${colors.bg.card};
  border-bottom: 1px solid ${colors.border}; font-size: ${fontSize.sm}; font-weight: 700; color: ${colors.text.primary};
  & .count { font-weight: 500; color: ${colors.text.muted}; flex: 1; }
  & .whole { font-weight: 500; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
`
const Row = styled.div<{ $depth: number; $eff: NodeState | null }>`
  display: flex; align-items: center; gap: 8px; min-height: 30px; padding: 2px 8px 2px ${({ $depth }) => 8 + $depth * 18}px;
  border-bottom: 1px solid ${colors.border};
  background: ${({ $eff }) => ($eff === 'deny' ? colors.red.bg : $eff === 'allow' ? colors.green.bg : $eff === 'change' ? colors.blue.bg : 'transparent')};
  &:last-child { border-bottom: 0; }
  & .chev { width: 16px; display: inline-flex; color: ${colors.text.muted}; flex-shrink: 0; border: 0; background: transparent; padding: 0; cursor: pointer; }
  & .label { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 8px; overflow: hidden; }
  & .name { font-size: ${fontSize.base}; color: ${colors.text.primary}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  & .runs { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  & .inh { font-size: ${fontSize.micro}; color: ${colors.text.muted}; white-space: nowrap; }
  & .eye { display: inline-flex; width: 18px; justify-content: center; flex-shrink: 0; }
`
const Legend = styled.div`
  display: flex; flex-wrap: wrap; gap: 4px 16px; margin-bottom: 8px; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & span { display: inline-flex; align-items: center; gap: 5px; }
`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.sm}; overflow: hidden; flex-shrink: 0; background: ${colors.bg.card};`
const SegBtn = styled.button<{ $active?: boolean; $tone?: 'allow' | 'change' | 'deny' | 'none' }>`
  display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 22px; border: 0; cursor: pointer;
  background: ${({ $active, $tone }) => (!$active ? 'transparent' : $tone === 'deny' ? colors.red.bg : $tone === 'allow' ? colors.green.bg : $tone === 'change' ? colors.blue.bg : colors.bg.input)};
  color: ${({ $active, $tone }) => (!$active ? colors.text.muted : $tone === 'deny' ? colors.red.main : $tone === 'allow' ? colors.green.main : $tone === 'change' ? colors.blue.main : colors.text.primary)};
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

  const word = (s: NodeState | null) => (s === 'allow' ? t('settings.access.stateRead') : s === 'change' ? t('settings.access.stateChange') : s === 'deny' ? t('settings.access.stateHidden') : t('settings.access.none'))

  const renderNode = (n: MenuNode, depth: number): React.ReactNode => {
    const e = effects[n.key] ?? { own: null, inherited: null, visible: null }
    const eff = e.own ?? e.inherited
    const leaf = isLeaf(n)
    const open = !collapsed.has(n.key)
    // what a tick on this entry gives, in numbers (the names on hover); without the server's answer, what it runs
    const gives = leaf && n.needs ? t('settings.access.gives', { read: n.needs.read.length, change: n.needs.change.length }) : null
    const runs = gives ?? (n.type === 'page' ? t('settings.access.page') : n.targetPerm ?? (n.type ? `${n.type} · ${n.target ?? ''}` : ''))
    const runsTitle = gives && n.needs
      ? [t('settings.access.givesRead'), ...n.needs.read, ...(n.needs.change.length ? ['', t('settings.access.givesChange'), ...n.needs.change] : [])].join('\n')
      : runs
    return (
      <div key={n.key}>
        <Row $depth={depth} $eff={eff} data-node={n.key}>
          {!leaf && n.children.length > 0
            ? <button type="button" className="chev" onClick={() => toggle(n.key)} title={open ? t('settings.access.collapse') : t('settings.access.expand')}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
            : <span className="chev" />}
          <span className="label">
            <span className="name" title={n.id}>{n.label}</span>
            {runs && <span className="runs" title={runsTitle}>{runs}</span>}
          </span>
          {e.own == null && <span className="inh">{t('settings.access.follows')}: {word(e.inherited)}</span>}
          <span className="eye" title={leaf ? (e.visible ? t('settings.access.shown') : t('settings.access.hidden')) : ''}>
            {leaf ? (e.visible ? <Eye size={13} color={colors.green.main} /> : <EyeOff size={13} color={colors.text.muted} />) : null}
          </span>
          <Seg>
            <SegBtn $active={e.own == null} $tone="none" title={t('settings.access.levelFollow')} onClick={() => onSetState(n.key, null)}><Minus size={12} /></SegBtn>
            <SegBtn $active={e.own === 'allow'} $tone="allow" title={t('settings.access.levelRead')} onClick={() => onSetState(n.key, 'allow')}><BookOpen size={12} /></SegBtn>
            <SegBtn $active={e.own === 'change'} $tone="change" title={t('settings.access.levelChange')} onClick={() => onSetState(n.key, 'change')}><Pencil size={12} /></SegBtn>
            <SegBtn $active={e.own === 'deny'} $tone="deny" title={t('settings.access.levelHidden')} onClick={() => onSetState(n.key, 'deny')}><Ban size={12} /></SegBtn>
          </Seg>
        </Row>
        {open && n.children.map((c) => renderNode(c, depth + 1))}
      </div>
    )
  }

  const apps = Object.entries(forest)
  if (apps.length === 0) return <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.access.noMenus')}</span>
  return (
    <div data-menu-tree>
      <Legend>
        <span><Minus size={12} /> {t('settings.access.levelFollow')}</span>
        <span style={{ color: colors.green.main }}><BookOpen size={12} /> {t('settings.access.levelRead')}</span>
        <span style={{ color: colors.blue.main }}><Pencil size={12} /> {t('settings.access.levelChange')}</span>
        <span style={{ color: colors.red.main }}><Ban size={12} /> {t('settings.access.levelHidden')}</span>
      </Legend>
      {apps.map(([app, roots]) => {
        const leaves = roots.flatMap(function count(n: MenuNode): MenuNode[] { return isLeaf(n) ? [n] : n.children.flatMap(count) })
        const shown = leaves.filter((l) => effects[l.key]?.visible).length
        const own = effects[appKey(app)]?.own ?? null
        return (
          <AppBlock key={app}>
            <AppHead data-node={appKey(app)}>
              {app} <span className="count">· {t('settings.access.shownCount', { shown, total: leaves.length })}</span>
              {/* the whole application: what every folder follows unless it says otherwise */}
              <span className="whole">{t('settings.access.wholeApp')}</span>
              <Seg>
                <SegBtn $active={own == null} $tone="none" title={t('settings.access.levelNone')} onClick={() => onSetState(appKey(app), null)}><Minus size={12} /></SegBtn>
                <SegBtn $active={own === 'allow'} $tone="allow" title={t('settings.access.levelRead')} onClick={() => onSetState(appKey(app), 'allow')}><BookOpen size={12} /></SegBtn>
                <SegBtn $active={own === 'change'} $tone="change" title={t('settings.access.levelChange')} onClick={() => onSetState(appKey(app), 'change')}><Pencil size={12} /></SegBtn>
                <SegBtn $active={own === 'deny'} $tone="deny" title={t('settings.access.levelHidden')} onClick={() => onSetState(appKey(app), 'deny')}><Ban size={12} /></SegBtn>
              </Seg>
            </AppHead>
            {roots.map((r) => renderNode(r, 0))}
          </AppBlock>
        )
      })}
    </div>
  )
}
