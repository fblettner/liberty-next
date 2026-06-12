// Shared proposals table for the "scan a table → dictionary items" flow — used by BOTH the
// Dictionary editor's scan modal (DictionaryScan) and the Screen Assistant's Dictionary step, so the
// two surfaces stay identical: existing items are shown greyed + unchecked ("exists"), only the
// missing ones are ticked to be created, and every proposed field (label / format / rule / rule
// value / lookup params / default) is editable. Presentational — the parent owns the state.
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Checkbox, Input, SearchSelect, Tag, type SearchSelectOption } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

// One row: the scanned column + its (editable) proposed dictionary definition. ``keep`` drives the
// create checkbox; ``exists`` disables it (the entry is already in the dictionary).
export interface ScanProposal {
  column: string                      // physical column — the stable row key
  dd_id: string                       // dictionary id (data item)
  exists: boolean
  type?: string | null
  source?: 'jde' | 'inferred'
  keep: boolean
  label: string
  format: string
  rules: string
  rules_values: string
  lookup_params: string               // "SY=01,RT=ST"
  default: string
  justify: string
  size: number | null
}

const Wrap = styled.div`overflow: auto; border: 1px solid ${colors.border}; border-radius: ${radius.md};`
const Table = styled.table`width: 100%; border-collapse: collapse; font-size: ${fontSize.base};`
const Th = styled.th`text-align: left; padding: 6px 8px; color: ${colors.text.muted}; font-weight: 600; font-size: ${fontSize.sm}; border-bottom: 1px solid ${colors.border}; position: sticky; top: 0; background: ${colors.bg.dropdown}; z-index: 1; white-space: nowrap;`
const Td = styled.td`padding: 5px 8px; border-bottom: 1px solid ${colors.border}; vertical-align: middle;`
const Mono = styled.span`font-family: ${fonts.mono}; color: ${colors.text.primary};`

export function summarizeProposals(items: ScanProposal[]): { missing: number; total: number } {
  return { missing: items.filter((i) => !i.exists).length, total: items.length }
}

export function ScanProposalsTable({
  items, jde, fmtOpts, ruleOpts, onPatch,
}: {
  items: ScanProposal[]
  jde: boolean
  fmtOpts: SearchSelectOption[]
  ruleOpts: SearchSelectOption[]
  onPatch: (column: string, patch: Partial<ScanProposal>) => void
}) {
  const { t } = useTranslation()
  return (
    <Wrap>
      <Table>
        <thead><tr>
          <Th style={{ width: 36 }} />
          <Th>{t('settings.dictscan.column', 'Column')}</Th>
          <Th>{t('settings.dictscan.label', 'Label')}</Th>
          <Th style={{ width: 120 }}>{t('settings.dictscan.format', 'Format')}</Th>
          <Th style={{ width: 130 }}>{t('settings.dictscan.rule', 'Rule')}</Th>
          <Th style={{ width: 96 }}>{t('settings.dictscan.ruleValue', 'Rule value')}</Th>
          <Th style={{ width: 140 }}>{t('settings.dictscan.lookupParams', 'Lookup params')}</Th>
          <Th style={{ width: 100 }}>{t('settings.dictscan.default', 'Default')}</Th>
          <Th style={{ width: 92 }}>{t('settings.dictscan.source', 'Source')}</Th>
        </tr></thead>
        <tbody>
          {items.map((d) => {
            const dis = d.exists
            return (
              <tr key={d.column} style={d.exists ? { opacity: 0.55 } : undefined}>
                <Td><Checkbox checked={d.keep} disabled={dis} onChange={(v) => onPatch(d.column, { keep: v })} /></Td>
                <Td>
                  <Mono>{d.dd_id}</Mono>
                  {d.exists && <span style={{ marginLeft: 6, fontSize: fontSize.micro, color: colors.text.muted }}>({t('settings.dictscan.exists', 'exists')})</span>}
                  <div style={{ fontSize: fontSize.micro, color: colors.text.muted }}>{d.column !== d.dd_id ? `${d.column} · ` : ''}{d.type ?? ''}</div>
                </Td>
                <Td><Input value={d.label} disabled={dis} onChange={(e) => onPatch(d.column, { label: e.target.value })} placeholder={d.dd_id} /></Td>
                <Td><SearchSelect value={d.format} disabled={dis} onChange={(v) => onPatch(d.column, { format: v })} options={fmtOpts} allowCustom placeholder="text" /></Td>
                <Td><SearchSelect value={d.rules} disabled={dis} onChange={(v) => onPatch(d.column, { rules: v })} options={ruleOpts} anyLabel={t('common.none', '(none)')} placeholder="—" /></Td>
                <Td><Input value={d.rules_values} disabled={dis || !d.rules} onChange={(e) => onPatch(d.column, { rules_values: e.target.value })} placeholder="—" /></Td>
                <Td><Input value={d.lookup_params} disabled={dis || d.rules !== 'LOOKUP'} onChange={(e) => onPatch(d.column, { lookup_params: e.target.value })} placeholder={d.rules === 'LOOKUP' ? 'SY=01,RT=ST' : ''} /></Td>
                <Td><Input value={d.default} disabled={dis} onChange={(e) => onPatch(d.column, { default: e.target.value })} placeholder="—" /></Td>
                <Td>{d.source === 'jde' ? <Tag $tone="orange">JDE&nbsp;DD</Tag> : <Tag $tone="blue">{jde ? t('settings.dictscan.inferred', 'inferred') : t('settings.dictscan.column', 'Column')}</Tag>}</Td>
              </tr>
            )
          })}
        </tbody>
      </Table>
    </Wrap>
  )
}
