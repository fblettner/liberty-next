// The assistant's starter questions of one application, in the language being edited.
//
// An application's block carries its questions as written (`starters`, `groups`, the screens'
// own) and, under `l.<language>`, the same three said in another language — each list given
// there replaces its counterpart for the users of that language. The AI builder shows one
// language at a time: these two functions read and write "the lists of the language shown".
import type { AiLocaleBody, AppAiBody } from '../types/config'

/** The lists edited when *language* is shown ('' = as written). */
export function starterView(block: AppAiBody, language: string): AiLocaleBody {
  if (!language) return { starters: block.starters, groups: block.groups, screens: block.screens }
  return block.l?.[language] ?? {}
}

const kept = (loc: AiLocaleBody): AiLocaleBody => {
  const out: AiLocaleBody = {}
  if (loc.starters?.length) out.starters = loc.starters
  if (loc.groups?.length) out.groups = loc.groups
  const screens = Object.fromEntries(Object.entries(loc.screens ?? {}).filter(([, s]) => s.starters?.length || s.groups?.length))
  if (Object.keys(screens).length) out.screens = screens
  return out
}

/** *block* with the lists of *language* changed by *patch*. As written ('') the block's own
 *  lists are set as given; in another language an emptied list is left out — the language then
 *  falls back on the list as written — and a language left with nothing is taken out. */
export function withStarters(block: AppAiBody, language: string, patch: Partial<AiLocaleBody>): AppAiBody {
  if (!language) return { ...block, ...patch }
  const loc = kept({ ...(block.l?.[language] ?? {}), ...patch })
  const l = { ...(block.l ?? {}) }
  if (Object.keys(loc).length) l[language] = loc
  else delete l[language]
  const next: AppAiBody = { ...block }
  if (Object.keys(l).length) next.l = l
  else delete next.l
  return next
}
