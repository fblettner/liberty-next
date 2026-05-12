// The "current app" picker in the top utility pill. Lists the *apps* you can access (connectors
// that have a menu — not the data-source pools they hang off); picking one filters the Connectors
// page / nav to it ("(all apps)" = no filter). Rendered only when there's more than one app — with
// one, "all" and that app show the same thing, so there's nothing to pick. Includes its own
// trailing separator so it leaves no orphan when it renders null.
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { colors, fontSize, fonts, radius } from '../theme'
import { useWorkspace } from '../workspace/WorkspaceContext'

const PillSelect = styled.select`
  height: 30px;
  max-width: 170px;
  padding: 0 6px;
  border-radius: ${radius.md};
  border: 1px solid transparent;
  background: transparent;
  color: ${colors.text.secondary};
  font-size: ${fontSize.sm};
  font-family: ${fonts.sans};
  font-weight: 500;
  cursor: pointer;
  outline: none;
  transition: background 0.15s, color 0.15s;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
  &:focus { color: ${colors.text.primary}; }
  & option { background: ${colors.bg.card}; color: ${colors.text.primary}; }
`

const Sep = styled.div`
  width: 1px;
  height: 16px;
  background: ${colors.border};
  margin: 0 2px;
`

export default function WorkspaceSelect() {
  const { t } = useTranslation()
  const { apps, currentApp, setCurrentApp } = useWorkspace()
  if (!apps || apps.length < 2) return null
  return (
    <>
      <PillSelect
        value={currentApp ?? ''}
        onChange={(e) => setCurrentApp(e.target.value || null)}
        title={t('workspace.app')}
      >
        <option value="">{t('workspace.allApps')}</option>
        {apps.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name}
          </option>
        ))}
      </PillSelect>
      <Sep />
    </>
  )
}
