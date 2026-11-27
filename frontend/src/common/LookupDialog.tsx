// A list offered AS A TABLE in a dialog — what a dropdown opens when its list is long or has several
// columns (services/options.browsable): the code, the label and each extra column under its heading, the
// same search and the same filter chips as the dropdown, with room to read. A line picked is the value.
// Opened by SearchSelect ("Open as a table"): every screen, form and filter that shows a list has it.
import { useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Check } from 'lucide-react'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter } from './Modal'
import { Button } from './Button'
import { Input } from './Input'
import { type Searchable, facetColumns, filterOptions } from '../services/options'
import { colors, fontSize, fonts, radius } from '../theme'

const Bar = styled.div`display: flex; flex-direction: column; gap: 8px; flex-shrink: 0;`
const Facets = styled.div`display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
  & .flbl { color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin-right: 4px; }`
const Chip = styled.button<{ $active?: boolean }>`
  padding: 2px 10px; border-radius: 999px; cursor: pointer; font-size: ${fontSize.micro}; font-family: ${fonts.mono};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  &:hover { border-color: ${colors.blue.border}; color: ${colors.text.primary}; }
`
const Frame = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden; display: flex; flex-direction: column; min-height: 0; flex: 1;`
const Line = styled.div<{ $head?: boolean; $active?: boolean }>`
  display: grid; gap: 12px; align-items: center; padding: ${({ $head }) => ($head ? '7px 12px' : '6px 12px')};
  font-size: ${({ $head }) => ($head ? fontSize.micro : fontSize.sm)}; font-family: ${fonts.sans};
  color: ${({ $head, $active }) => ($head ? colors.text.muted : $active ? colors.blue.main : colors.text.secondary)};
  background: ${({ $head, $active }) => ($head ? colors.bg.input : $active ? colors.blue.bg : 'transparent')};
  border-bottom: 1px solid ${colors.border};
  ${({ $head }) => ($head ? 'text-transform: uppercase; letter-spacing: 0.05em; flex-shrink: 0;' : 'cursor: pointer;')}
  & > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .mono { font-family: ${fonts.mono}; color: ${({ $head }) => ($head ? colors.text.muted : colors.text.primary)}; }
  ${({ $head }) => ($head ? '' : '&:hover { background: var(--hover-subtle); }')}
`
const Rows = styled.div`flex: 1; min-height: 0; overflow-y: auto;`
const Count = styled.span`flex: 1; font-size: ${fontSize.sm}; color: ${colors.text.muted};`

export function LookupDialog<T extends Searchable>({
  title, options, value, cellColumns, onPick, onClose,
}: {
  title?: string
  options: T[]
  value: string
  cellColumns?: { label: string; filterable?: boolean }[]
  onPick: (value: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const [facets, setFacets] = useState<Record<number, string>>({})
  const facetCols = useMemo(() => facetColumns(cellColumns, options), [cellColumns, options])
  const shown = useMemo(() => filterOptions(options, text, facets), [options, text, facets])
  const coded = options.some((o) => o.mono && o.mono !== o.label)
  const extra = options.reduce((n, o) => Math.max(n, o.cells?.length ?? 0), 0)
  const columns = `${coded ? 'minmax(90px, 0.9fr) ' : ''}minmax(160px, 2.2fr)${' minmax(80px, 1fr)'.repeat(extra)} 18px`
  const listRef = useRef<HTMLDivElement>(null)
  const virt = useVirtualizer({ count: shown.length, getScrollElement: () => listRef.current, estimateSize: () => 31, overscan: 16 })

  return (
    <Overlay onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <Modal style={{ width: `min(${720 + extra * 130}px, 95vw)` }} data-lookup-dialog>
        <ModalHeader>{title || t('lookupDialog.title')}</ModalHeader>
        <ModalBody style={{ display: 'flex', flexDirection: 'column', gap: 10, height: 'min(68vh, 640px)' }}>
          <Bar>
            <Input autoFocus value={text} placeholder={t('table.search')} onChange={(e) => setText(e.target.value)} />
            {facetCols.map((fc) => (
              <Facets key={fc.index}>
                <span className="flbl">{fc.label}</span>
                {fc.values.map((v) => (
                  <Chip key={v} type="button" $active={facets[fc.index] === v}
                    onClick={() => setFacets((prev) => { const next = { ...prev }; if (next[fc.index] === v) delete next[fc.index]; else next[fc.index] = v; return next })}>{v}</Chip>
                ))}
              </Facets>
            ))}
          </Bar>
          <Frame>
            <Line $head style={{ gridTemplateColumns: columns }}>
              {coded && <span>{t('lookupDialog.code')}</span>}
              <span>{t('lookupDialog.label')}</span>
              {Array.from({ length: extra }, (_x, i) => <span key={i}>{cellColumns?.[i]?.label ?? ''}</span>)}
              <span />
            </Line>
            <Rows ref={listRef}>
              <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
                {virt.getVirtualItems().map((vi) => {
                  const o = shown[vi.index]
                  return (
                    <Line key={o.value} $active={o.value === value} data-lookup-row={o.value}
                      style={{ gridTemplateColumns: columns, position: 'absolute', top: 0, left: 0, right: 0, transform: `translateY(${vi.start}px)` }}
                      onClick={() => onPick(o.value)}>
                      {coded && <span className="mono">{o.mono ?? ''}</span>}
                      <span title={o.label}>{o.label}</span>
                      {Array.from({ length: extra }, (_x, i) => <span key={i} title={o.cells?.[i] ?? ''}>{o.cells?.[i] ?? ''}</span>)}
                      <span>{o.value === value ? <Check size={13} /> : null}</span>
                    </Line>
                  )
                })}
              </div>
              {shown.length === 0 && <div style={{ padding: 16, color: colors.text.muted, fontSize: fontSize.sm }}>{t('table.noMatches')}</div>}
            </Rows>
          </Frame>
        </ModalBody>
        <ModalFooter>
          <Count>{t('lookupDialog.count', { n: shown.length, total: options.length })}</Count>
          <Button $variant="ghost" $size="sm" onClick={onClose}>{t('common.close')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
