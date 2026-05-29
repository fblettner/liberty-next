// The "current app" picker — lives in the sidebar (under the brand). Lists the *apps* you can
// access (connectors that have a menu — not the data-source pools they hang off); picking one
// switches the sidebar menu + workspace tabs to that app. Rendered only when there's more than
// one app — with one, there's nothing to pick. Reuses the standard `SearchSelect` so it stays
// consistent with the rest of the dropdowns (themed popover, search, keyboard-friendly).
//
// The "(all apps)" entry was dropped per operator feedback — it served nothing in practice (the
// sidebar already shows every app's tree when no app is picked, just stacked; the picker should
// be a hard switch, not a soft filter). When currentApp is null (legacy localStorage), the
// picker auto-initialises to the first app so the displayed value always matches state.
//
// `collapsed` hides the picker UI on the narrow rail (a search dropdown can't fit 56px) — but the
// component stays mounted so the auto-init effect below still runs (the current app must resolve
// even when the operator loads with the sidebar collapsed).
import { useEffect } from 'react'
import styled from '@emotion/styled'
import { SearchSelect } from '../common'
import { useWorkspace } from '../workspace/WorkspaceContext'

const Wrap = styled.div`
  display: flex;
  padding: 0 8px;
  margin: 0 0 10px;
`

export default function WorkspaceSelect({ collapsed = false }: { collapsed?: boolean }) {
  const { apps, currentApp, setCurrentApp } = useWorkspace()
  // Auto-init when apps loaded but no app picked yet — drops the "(all apps)" middle state
  // so the picker always reflects a real app (the operator picks across apps, never "none").
  useEffect(() => {
    if (apps && apps.length > 0 && !currentApp) {
      setCurrentApp(apps[0].name)
    }
  }, [apps, currentApp, setCurrentApp])
  if (collapsed || !apps || apps.length < 2) return null
  const options = apps.map((c) => ({ value: c.name, label: c.name }))
  return (
    <Wrap>
      <SearchSelect
        value={currentApp ?? ''}
        // Always switch to a real app — there's no "no selection" option anymore,
        // so v is always a valid app name (never empty string).
        onChange={(v) => { if (v) setCurrentApp(v) }}
        options={options}
      />
    </Wrap>
  )
}
