// "Find": every saved analysis and dashboard of the models the user may use, in one list — an
// object is saved under one model and the page lists those of the model shown, so one saved
// elsewhere was only found by trying each model. Typed words narrow the list (name, description,
// audience, owner, model); picking a line opens it, switching to its model when it is another.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { FlaskConical, LayoutDashboard, Search } from 'lucide-react'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Input, Tag } from '../../common'
import { groupFound, matchSaved, type FoundSaved, type SavedKind } from '../../services/explorerSaved'
import { colors, fontSize, fonts, radius } from '../../theme'

const Bar = styled.div`display: flex; gap: 8px; align-items: center; margin-bottom: 10px;`
const SearchBox = styled.div`position: relative; flex: 1; & svg { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: ${colors.text.muted}; } & input { padding-left: 32px; width: 100%; }`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden; flex-shrink: 0;`
const SegBtn = styled.button<{ $active: boolean }>`
  padding: 6px 10px; border: 0; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${(p) => (p.$active ? colors.blue.bg : 'transparent')}; color: ${(p) => (p.$active ? colors.blue.main : colors.text.secondary)};
`
const List = styled.div`display: flex; flex-direction: column; gap: 10px; max-height: min(60vh, 560px); overflow-y: auto; padding-right: 2px;`
const Group = styled.div`
  font-family: ${fonts.sans}; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: ${colors.text.muted};
  display: flex; align-items: baseline; gap: 8px; padding: 2px 2px 4px;
  & .here { font-weight: 400; letter-spacing: 0; text-transform: none; color: ${colors.blue.main}; }
  & .n { font-weight: 400; margin-left: auto; letter-spacing: 0; text-transform: none; }
`
const Row = styled.button`
  display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; cursor: pointer;
  padding: 7px 10px; border-radius: ${radius.md}; border: 1px solid ${colors.border}; background: ${colors.bg.input}; margin-bottom: 4px;
  font-family: ${fonts.sans}; color: ${colors.text.primary}; min-width: 0;
  & > svg { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-size: ${fontSize.sm}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .more { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .when { flex-shrink: 0; font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-variant-numeric: tabular-nums; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; padding: 18px 4px; text-align: center;`

export function FindSavedModal({ found, current, appLabel, onOpen, onClose }: {
  found: FoundSaved[]
  /** the model in view, `app/model`: its group says so */
  current: string
  /** an application as its menu names it */
  appLabel: (app: string) => string
  onOpen: (item: FoundSaved) => void
  onClose: () => void
}) {
  const { t, i18n } = useTranslation()
  const [text, setText] = useState('')
  const [kind, setKind] = useState<SavedKind | 'all'>('all')
  // the window opens to be typed in
  const box = useRef<HTMLInputElement>(null)
  useEffect(() => { box.current?.focus() }, [])
  const groups = useMemo(() => groupFound(matchSaved(found, text, kind)), [found, text, kind])
  const shown = groups.reduce((n, g) => n + g.items.length, 0)
  const day = (iso?: string | null) => { if (!iso) return ''; const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(i18n.language) }
  return (
    <Overlay onClick={onClose}>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(760px, 94vw)' }} data-find-saved-modal>
        <ModalHeader>{t('explorer.find.title')}</ModalHeader>
        <ModalBody>
          <Bar>
            <SearchBox><Search size={14} /><Input ref={box} value={text} onChange={(e) => setText(e.target.value)} placeholder={t('explorer.find.placeholder')} data-find-saved-input /></SearchBox>
            <Seg>
              {(['all', 'analyses', 'dashboards'] as const).map((k) => (
                <SegBtn key={k} type="button" $active={kind === k} onClick={() => setKind(k)}>{t(k === 'all' ? 'explorer.find.all' : k === 'analyses' ? 'explorer.analyses' : 'explorer.dashboards')}</SegBtn>
              ))}
            </Seg>
          </Bar>
          <List>
            {shown === 0 && <Empty>{t(found.length ? 'explorer.find.none' : 'explorer.find.nothingSaved')}</Empty>}
            {groups.map((g) => (
              <div key={g.key} data-find-saved-group={g.key}>
                <Group>
                  <span>{appLabel(g.app)} · {g.label}</span>
                  {g.key === current && <span className="here">{t('explorer.find.thisModel')}</span>}
                  <span className="n">{g.items.length}</span>
                </Group>
                {g.items.map((f) => (
                  <Row key={`${f.kind}:${f.id}`} type="button" data-find-saved-item={`${g.key}:${f.kind}:${f.id}`} onClick={() => onOpen(f)}>
                    {f.kind === 'dashboards' ? <LayoutDashboard size={15} /> : <FlaskConical size={15} />}
                    <span className="text">
                      <span className="name">{f.name}</span>
                      {(f.description || f.audience) && <span className="more">{[f.audience, f.description].filter(Boolean).join(' · ')}</span>}
                    </span>
                    {!f.shared && <Tag>{t('explorer.mine')}</Tag>}
                    <span className="when">{day(f.updated_at)}</span>
                  </Row>
                ))}
              </div>
            ))}
          </List>
        </ModalBody>
        <ModalFooter>
          <span style={{ marginRight: 'auto', color: colors.text.muted, fontSize: fontSize.sm, fontFamily: fonts.sans }}>{t('explorer.find.count', { n: shown, total: found.length })}</span>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.close')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
