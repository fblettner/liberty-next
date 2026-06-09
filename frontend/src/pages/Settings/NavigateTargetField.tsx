// The target picker for a `navigate` action — a Query | Screen type toggle + one dropdown that
// swaps its source by the choice (mirrors the menu editor). Shared by ActionListEditor and the
// Screen Designer's ActionTreeView.
//
// Why the type lives in LOCAL state, not the action data: the action-patch path strips empty
// values (updateAtPath / setProp drop '' / null), so "Screen mode, nothing picked yet" can't be
// represented by an empty `screen` field — it would immediately collapse back to Query. Keying
// the toggle on local state (seeded from the action on mount) keeps the chosen mode stable while
// the operator picks a value. The PARENT must give this a stable `key` per action so switching to
// another action re-seeds the mode.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Field, Row, SearchSelect, type SearchSelectOption } from '../../common'
import { Edit3 } from 'lucide-react'

type ActionRow = Record<string, unknown>

export function NavigateTargetField({
  action, onPatch, actionConn, hasConnector, queryOptions, screenOptions, onEditQuery,
}: {
  action: ActionRow
  onPatch: (patch: ActionRow) => void
  actionConn: string
  /** Whether the target connector resolved (drives the picker's placeholder / loading state). */
  hasConnector: boolean
  queryOptions: SearchSelectOption[]
  screenOptions: SearchSelectOption[]
  onEditQuery: (connector: string, queryName: string) => void
}) {
  const { t } = useTranslation()
  // Seed from the action: a saved screen target has a truthy `screen`; otherwise it's a query.
  const [navType, setNavType] = useState<'query' | 'screen'>(action.screen ? 'screen' : 'query')

  const pickPlaceholder = hasConnector ? t('common.pick') : t('settings.screens.editor.queries.pickConnectorFirst')

  return (
    <>
      <Field label={t('settings.screens.action.navTargetType', 'Target type')}>
        <SearchSelect
          value={navType}
          options={[
            { value: 'query', label: t('settings.screens.action.navTargetQuery', 'Query') },
            { value: 'screen', label: t('settings.screens.action.navTargetScreen', 'Screen') },
          ]}
          onChange={(v) => {
            const next = v === 'screen' ? 'screen' : 'query'
            setNavType(next)
            // Clear the other side so exactly one of `to` / `screen` is set (both stripped if empty).
            onPatch(next === 'screen' ? { to: null } : { screen: null })
          }}
        />
      </Field>
      {navType === 'screen' ? (
        <Field label={t('settings.screens.action.navTargetScreen', 'Screen') + ' *'}>
          <SearchSelect
            value={(action.screen as string | undefined) ?? ''}
            options={screenOptions}
            onChange={(v) => onPatch({ screen: v || null })}
            placeholder={pickPlaceholder}
            loading={!hasConnector}
          />
        </Field>
      ) : (
        <Field label={t('settings.screens.action.navTargetQuery', 'Query') + ' *'}>
          <Row gap={6} style={{ alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <SearchSelect
                value={(action.to as string | undefined) ?? ''}
                options={queryOptions}
                onChange={(v) => onPatch({ to: v || null })}
                placeholder={pickPlaceholder}
                loading={!hasConnector}
              />
            </div>
            {typeof action.to === 'string' && action.to && actionConn && (
              <Button $variant="ghost" $size="sm" onClick={() => onEditQuery(actionConn, String(action.to))} title={t('settings.editQuery.edit', 'Edit query')}>
                <Edit3 size={13} />
              </Button>
            )}
          </Row>
        </Field>
      )}
    </>
  )
}
