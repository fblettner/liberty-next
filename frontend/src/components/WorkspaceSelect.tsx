// The "current app" picker in the top utility pill. Lists the *apps* you can access (connectors
// that have a menu — not the data-source pools they hang off); picking one filters the Connectors
// page / nav to it ("(all apps)" = no filter). Rendered only when there's more than one app — with
// one, "all" and that app show the same thing, so there's nothing to pick. Reuses the standard
// `SearchSelect` so it stays consistent with the rest of the dropdowns (themed popover, search,
// keyboard-friendly) instead of a bare <select>.
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { SearchSelect } from '../common'
import { colors } from '../theme'
import { useWorkspace } from '../workspace/WorkspaceContext'

// The utility bar is a fixed-height flex row; the SearchSelect's default `flex: 1` would let
// it grow to fill it. Pin a comfortable width here so it stays compact next to the EN/FR pill.
const Wrap = styled.div`
  width: 170px;
  display: flex;
`

const Sep = styled.div`
  width: 1px;
  height: 16px;
  background: ${colors.border};
  margin: 0 4px;
`

export default function WorkspaceSelect() {
  const { t } = useTranslation()
  const { apps, currentApp, setCurrentApp } = useWorkspace()
  if (!apps || apps.length < 2) return null
  const options = apps.map((c) => ({ value: c.name, label: c.name }))
  return (
    <>
      <Wrap>
        <SearchSelect
          value={currentApp ?? ''}
          onChange={(v) => setCurrentApp(v || null)}
          options={options}
          anyLabel={t('workspace.allApps')}
          placeholder={t('workspace.allApps')}
        />
      </Wrap>
      <Sep />
    </>
  )
}
