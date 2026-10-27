// The role's data-scope rules (Settings → Access → Roles → Data scope): the list of rules, each
// "item in / not in values" or "item = a user attribute", with the mode for queries without the
// item and an optional connector; plus the form that adds or edits one. Rules AND together
// within the role; roles OR together on the user (liberty/auth/scope.py).
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Plus, Trash2, X } from 'lucide-react'
import { Button, Checkbox, Field, SearchSelect, Tag, type SearchSelectOption } from '../../../common'
import { useWorkspace } from '../../../workspace/WorkspaceContext'
import { lookupOptions, useLookupTables, lookupKey } from '../../../services/lookups'
import type { DictionaryDoc } from '../../../types/config'
import { colors, fontSize, fonts, radius } from '../../../theme'
import { PermChip } from './OtherAccess'
import { attributeOf, cleanRule, describeRule, dictionaryItems, itemLabels, ruleTarget, sourceFor, valueSourceFor, type DictionaryItem, type ScopeRule } from './scopeRules'

const Rows = styled.div`display: flex; flex-direction: column; gap: 6px;`
const RuleRow = styled.div<{ $editing?: boolean }>`
  display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${({ $editing }) => ($editing ? colors.blue.border : colors.border)}; background: ${({ $editing }) => ($editing ? colors.blue.bg : colors.bg.input)};
  &:hover { border-color: ${colors.blue.border}; }
  & .text { flex: 1; min-width: 0; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .and { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; width: 28px; text-align: center; flex-shrink: 0; }
  & button.icon { border: 0; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 2px; }
  & button.icon:hover { color: ${colors.text.primary}; }
`
const Form = styled.div`
  margin-top: 10px; padding: 12px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card};
  display: flex; flex-direction: column; gap: 10px;
  & .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  & .inline { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean }>`
  padding: 5px 10px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')}; color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 5px; align-items: center;`

interface Props {
  value: ScopeRule[]
  onChange: (next: ScopeRule[]) => void
  dictionary: DictionaryDoc | null
  attributeNames: string[]
  columnNames: Record<string, string[]>
}

const EMPTY: ScopeRule = { dd: '', values: [], exclude: false, mode: 'ignore', connector: null }

export default function ScopeRulesEditor({ value, onChange, dictionary, attributeNames, columnNames }: Props) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  const items = useMemo(() => dictionaryItems(dictionary), [dictionary])
  const labels = useMemo(() => itemLabels(items), [items])
  const [editing, setEditing] = useState<number | null>(null)   // index being edited; null = adding
  const [draft, setDraft] = useState<ScopeRule>(EMPTY)
  const [byAttribute, setByAttribute] = useState(false)
  const [byColumn, setByColumn] = useState(false)
  const [valueText, setValueText] = useState('')

  const sqlConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql'), [connectors])
  const target = ruleTarget(draft)
  const item: DictionaryItem | undefined = useMemo(
    () => items.find((i) => i.key === target && (!draft.connector || i.section === draft.connector)) ?? items.find((i) => i.key === target),
    [items, target, draft.connector],
  )
  const source = useMemo(() => valueSourceFor(item, dictionary), [item, dictionary])
  const tables = useLookupTables(source?.kind === 'lookup' ? [source.spec] : [])
  const valueOpts = useMemo<SearchSelectOption[]>(() => {
    if (source?.kind === 'enum') return source.options.map((o) => ({ value: o.value, label: o.label, mono: o.value }))
    if (source?.kind === 'lookup') {
      const data = tables.get(lookupKey(source.spec))
      return data ? lookupOptions(data) : []
    }
    return []
  }, [source, tables])

  const itemOpts = useMemo<SearchSelectOption[]>(() => items.map((i) => ({
    value: `${i.section}\u0000${i.key}`, label: i.label ? `${i.key} — ${i.label}` : i.key, mono: i.key,
    group: i.section === 'shared' ? t('settings.access.sharedDictionary', 'Shared dictionary') : i.section,
  })), [items, t])
  const itemValue = item ? `${item.section}\u0000${item.key}` : ''
  const pickItem = (v: string) => {
    const key = v.includes('\u0000') ? v.split('\u0000')[1] : v.trim().toUpperCase()
    setDraft((d) => ({ ...d, dd: key, column: null, values: [] }))
    setValueText('')
  }
  // Columns without a dictionary item: the names the connector's screens declare (all connectors
  // when the rule names none), plus anything typed.
  const columnOpts = useMemo<SearchSelectOption[]>(() => {
    const conns = draft.connector ? [draft.connector] : Object.keys(columnNames).sort()
    return conns.flatMap((c) => (columnNames[c] ?? []).map((name) => ({ value: name, label: name, mono: name, group: c })))
      .filter((o, i, arr) => arr.findIndex((x) => x.value === o.value) === i)
  }, [columnNames, draft.connector])
  const pickColumn = (v: string) => {
    const key = v.trim().toUpperCase()
    setDraft((d) => ({ ...d, dd: null, column: key, values: [] }))
    setValueText('')
  }
  const switchTarget = (column: boolean) => { setByColumn(column); setDraft((d) => ({ ...d, dd: null, column: null, values: [] })) }

  const start = (idx: number | null) => {
    setEditing(idx)
    const r = idx == null ? EMPTY : value[idx]
    setDraft({ ...EMPTY, ...r, values: [...(r.values ?? [])] })
    setByAttribute(!!attributeOf(r.source))
    setByColumn(!!r.column && !r.dd)
    setValueText('')
  }
  const addValue = (v: string) => {
    const s = v.trim()
    if (!s) return
    setDraft((d) => (d.values ?? []).includes(s) ? d : { ...d, values: [...(d.values ?? []), s] })
    setValueText('')
  }
  const commit = () => {
    if (!target) return
    const rule = cleanRule({ ...draft, source: byAttribute ? draft.source : null, values: byAttribute ? null : draft.values })
    if (byAttribute && !rule.source) return
    const next = [...value]
    if (editing == null) next.push(rule); else next[editing] = rule
    onChange(next)
    start(null); setEditing(null)
  }
  const remove = (idx: number) => { onChange(value.filter((_, i) => i !== idx)); if (editing === idx) start(null) }

  const attrOpts: SearchSelectOption[] = attributeNames.map((a) => ({ value: a, label: a, mono: sourceFor(a) }))
  const connOpts: SearchSelectOption[] = sqlConns.map((c) => ({ value: c.name, label: c.name, mono: c.name }))

  return (
    <div>
      <Rows>
        {value.map((r, idx) => (
          <RuleRow key={idx} $editing={editing === idx} role="button" tabIndex={0} onClick={() => start(idx)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); start(idx) } }}>
            <span className="and">{idx === 0 ? '' : t('settings.access.and', 'and')}</span>
            <span className="text" title={describeRule(r, labels)}>{describeRule(r, labels)}</span>
            {r.mode === 'deny' && <Tag $tone="red">{t('settings.access.denyAbsent', 'deny if absent')}</Tag>}
            {r.connector && <Tag $tone="blue">{r.connector}</Tag>}
            <button type="button" className="icon" title={t('common.delete')} onClick={(e) => { e.stopPropagation(); remove(idx) }}><Trash2 size={13} /></button>
          </RuleRow>
        ))}
        {value.length === 0 && <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.access.noScope', 'No rule: this role sees every row.')}</span>}
      </Rows>

      <Form>
        <div className="inline">
          <span style={{ fontSize: fontSize.sm, fontWeight: 700, color: colors.text.primary }}>{editing == null ? t('settings.access.newRule', 'New rule') : t('settings.access.editRule', 'Edit rule')}</span>
          <Seg>
            <SegBtn $active={!byColumn} onClick={() => switchTarget(false)}>{t('settings.access.byItem', 'Dictionary item')}</SegBtn>
            <SegBtn $active={byColumn} onClick={() => switchTarget(true)}>{t('settings.access.byColumn', 'Column name')}</SegBtn>
          </Seg>
        </div>
        <div className="grid">
          {byColumn ? (
            <Field label={t('settings.access.scopeColumn', 'Column (from the screens, or type one)')}>
              <SearchSelect value={draft.column ?? ''} onChange={pickColumn} options={columnOpts} allowCustom placeholder="COMPANY" />
            </Field>
          ) : (
            <Field label={t('settings.access.scopeItem', 'Dictionary item')}>
              <SearchSelect value={itemValue} onChange={pickItem} options={itemOpts} allowCustom placeholder={t('settings.access.pickItemScope', 'CO — Company…')} />
            </Field>
          )}
          <Field label={t('settings.access.scopeConnector', 'Connector')}>
            <SearchSelect value={draft.connector ?? ''} onChange={(v) => setDraft((d) => ({ ...d, connector: v || null }))} options={connOpts} anyLabel={t('settings.access.anyConnector', 'Any connector')} />
          </Field>
        </div>
        <div className="inline">
          <Seg>
            <SegBtn $active={!byAttribute} onClick={() => setByAttribute(false)}>{t('settings.access.fixedValues', 'Fixed values')}</SegBtn>
            <SegBtn $active={byAttribute} onClick={() => setByAttribute(true)}>{t('settings.access.fromAttribute', "From the user's attribute")}</SegBtn>
          </Seg>
          <Checkbox checked={!!draft.exclude} onChange={(v: boolean) => setDraft((d) => ({ ...d, exclude: v }))} label={t('settings.access.exclude', 'Exclude these values instead')} />
        </div>
        {byAttribute ? (
          <Field label={t('settings.access.attribute', 'User attribute')}>
            <SearchSelect value={attributeOf(draft.source) ?? ''} onChange={(v) => setDraft((d) => ({ ...d, source: v ? sourceFor(v.trim()) : null }))} options={attrOpts} allowCustom placeholder="company" />
          </Field>
        ) : (
          <Field label={t('settings.access.values', 'Values')}>
            <Chips>
              {(draft.values ?? []).map((v) => (
                <PermChip key={v} $deny={!!draft.exclude}>{v}{valueOpts.find((o) => o.value === v)?.label && valueOpts.find((o) => o.value === v)!.label !== v ? ` · ${valueOpts.find((o) => o.value === v)!.label}` : ''}
                  <button type="button" onClick={() => setDraft((d) => ({ ...d, values: (d.values ?? []).filter((x) => x !== v) }))}><X size={11} /></button></PermChip>
              ))}
              <div style={{ minWidth: 240, flex: 1 }}>
                <SearchSelect value={valueText} onChange={addValue} options={valueOpts.filter((o) => !(draft.values ?? []).includes(o.value))} allowCustom
                  placeholder={valueOpts.length ? t('settings.access.pickValue', 'Pick or type a value…') : t('settings.access.typeValue', 'Type a value and press Enter')} />
              </div>
            </Chips>
          </Field>
        )}
        <div className="inline">
          <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.access.whenAbsent', 'A query without this item:')}</span>
          <Seg>
            <SegBtn $active={draft.mode !== 'deny'} onClick={() => setDraft((d) => ({ ...d, mode: 'ignore' }))}>{t('settings.access.modeIgnore', 'is unrestricted')}</SegBtn>
            <SegBtn $active={draft.mode === 'deny'} onClick={() => setDraft((d) => ({ ...d, mode: 'deny' }))}>{t('settings.access.modeDeny', 'returns nothing')}</SegBtn>
          </Seg>
          <div style={{ flex: 1 }} />
          {editing != null && <Button $size="sm" $variant="ghost" onClick={() => { setEditing(null); start(null) }}>{t('common.cancel', 'Cancel')}</Button>}
          <Button $size="sm" $variant="primary" onClick={commit} disabled={!target || (byAttribute && !draft.source)}>
            {editing == null ? <><Plus size={13} /> {t('settings.access.addRule', 'Add')}</> : t('settings.access.updateRule', 'Update rule')}
          </Button>
        </div>
      </Form>
    </div>
  )
}
