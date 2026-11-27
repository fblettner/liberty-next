// The three settings that give a declared parameter its LIST (ParamDef.lookup…): the dictionary lookup its
// value is picked in, the connector that lookup belongs to, and what narrows the list (a :parameter of the
// lookup's query = another parameter here, or a fixed value). Used by the endpoint editor's parameter card.
// The lookups offered are the dictionary's: the shared ones and those of each connector.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import { SearchSelect } from '../../common'
import { PairsField } from '../TableView/PairsField'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { pairsObject, pairsText } from '../../services/appTests'
import type { DictionaryDoc } from '../../types/config'

let dictionaryOnce: Promise<DictionaryDoc | null> | null = null

export interface ParamLookupValue { lookup?: string | null; lookup_connector?: string | null; lookup_binds?: Record<string, string> | null }

export function ParamLookupFields({ value, onChange, Row }: {
  value: ParamLookupValue
  onChange: (next: ParamLookupValue) => void
  /** the editor's own labelled row: `<Row><label>…</label>field</Row>` */
  Row: React.ComponentType<{ style?: React.CSSProperties; children?: React.ReactNode }>
}) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  const [doc, setDoc] = useState<DictionaryDoc | null>(null)
  useEffect(() => {
    dictionaryOnce = dictionaryOnce ?? api.get<DictionaryDoc>('/admin/config/dictionary/parsed').catch(() => null)
    let on = true
    void dictionaryOnce.then((d) => { if (on) setDoc(d) })
    return () => { on = false }
  }, [])
  const scope = value.lookup_connector || ''
  const lookups = useMemo(() => {
    const out = new Map<string, string>()
    const add = (section: Record<string, Record<string, unknown>> | undefined, where: string) => {
      for (const [id, rec] of Object.entries(section ?? {})) if (!out.has(id)) out.set(id, [typeof rec.description === 'string' ? rec.description : '', where].filter(Boolean).join(' · '))
    }
    const d = doc?.dictionary
    if (scope) add(d?.connectors?.[scope]?.lookups, scope)
    add(d?.lookups, '')
    if (!scope) for (const [name, section] of Object.entries(d?.connectors ?? {})) add(section.lookups, name)
    return [...out.entries()].map(([id, label]) => ({ value: id, label: label || id, mono: id }))
  }, [doc, scope])
  const sqlConnectors = (connectors ?? []).filter((c) => c.type === 'sql').map((c) => ({ value: c.name, label: c.name }))
  return (
    <>
      <Row><label>{t('settings.api.endpoint.paramLookup')}</label>
        <SearchSelect value={value.lookup ?? ''} options={lookups} sort clearable allowCustom
          onChange={(v) => onChange({ ...value, lookup: v || undefined, ...(v ? {} : { lookup_binds: undefined }) })} /></Row>
      <Row><label>{t('settings.api.endpoint.paramLookupConnector')}</label>
        <SearchSelect value={value.lookup_connector ?? ''} options={sqlConnectors} sort clearable disabled={!value.lookup}
          onChange={(v) => onChange({ ...value, lookup_connector: v || undefined })} /></Row>
      {value.lookup && (
        <Row style={{ gridColumn: '1 / -1' }}><label>{t('settings.api.endpoint.paramLookupBinds')}</label>
          <PairsField value={pairsText(value.lookup_binds ?? {})} onChange={(text) => { const o = pairsObject(text); onChange({ ...value, lookup_binds: o && Object.keys(o).length ? (o as Record<string, string>) : undefined }) }} />
        </Row>
      )}
    </>
  )
}
