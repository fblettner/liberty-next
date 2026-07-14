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
import { Database } from 'lucide-react'
import { SearchSelect } from '../common'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { colors, fontSize } from '../theme'

const Wrap = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 8px;
  margin: 0 0 10px;
`
// The pool picker (multi-environment). A small "Pool" label + a searchable select of the
// current app's pools; changing it re-runs every screen against the chosen DB instance.
const PoolLabel = styled.span`
  display: inline-flex; align-items: center; gap: 5px;
  font-size: ${fontSize.sm}; color: ${colors.text.muted}; margin: 2px 0 -2px 2px;
`

export default function WorkspaceSelect({ collapsed = false }: { collapsed?: boolean }) {
  const { apps, currentApp, setCurrentApp, currentPoolOptions, currentPool, setCurrentPool } = useWorkspace()
  // Auto-init when apps loaded but no app picked yet — drops the "(all apps)" middle state
  // so the picker always reflects a real app (the operator picks across apps, never "none").
  // Runs even when collapsed (component stays mounted) so the current app always resolves.
  useEffect(() => {
    if (apps && apps.length > 0 && !currentApp) {
      setCurrentApp(apps[0].name)
    }
  }, [apps, currentApp, setCurrentApp])

  if (collapsed) return null
  const showApps = !!apps && apps.length >= 2
  const showPools = currentPoolOptions.length > 1  // the app spans >1 pool → offer a picker
  if (!showApps && !showPools) return null

  return (
    <Wrap>
      {showApps && (
        <SearchSelect
          value={currentApp ?? ''}
          // Always switch to a real app — there's no "no selection" option anymore,
          // so v is always a valid app name (never empty string).
          onChange={(v) => { if (v) setCurrentApp(v) }}
          options={apps!.map((c) => ({ value: c.name, label: c.name }))}
        />
      )}
      {showPools && (
        <div>
          <PoolLabel><Database size={12} /> Pool</PoolLabel>
          <SearchSelect
            value={currentPool ?? ''}
            // '' = "Default (per connector)" → no X-Liberty-Pool header; each connector uses its
            // own default. A named pool forces that environment on the connectors that have it.
            onChange={(v) => setCurrentPool(v || null)}
            options={[
              { value: '', label: 'Default' },
              ...currentPoolOptions.map((p) => ({ value: p, label: p })),
            ]}
          />
        </div>
      )}
    </Wrap>
  )
}
