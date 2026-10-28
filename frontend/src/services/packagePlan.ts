// The package plan (types/packagePlan.ts) as the views show it: states in a fixed order, the kind
// split from its scope, what an apply would change. No React here.
import type { PackagePlan, PlanEntity, PlanState } from '../types/packagePlan'

/** The order entities are listed in: what changes first, what stays last. */
export const STATE_ORDER: PlanState[] = ['changed', 'new', 'removed', 'skipped', 'kept', 'identical']

/** ``query/foo`` → ``{ kind: 'query', scope: 'foo' }``; ``job`` → ``{ kind: 'job', scope: null }``. */
export function splitKind(kind: string): { kind: string; scope: string | null } {
  const i = kind.indexOf('/')
  return i < 0 ? { kind, scope: null } : { kind: kind.slice(0, i), scope: kind.slice(i + 1) }
}

/** The file's entities by state (STATE_ORDER), then kind, then name — identical ones only when asked. */
export function shownEntities(entities: PlanEntity[] | undefined, showIdentical: boolean): PlanEntity[] {
  const rank = (s: PlanState) => STATE_ORDER.indexOf(s)
  return (entities ?? [])
    .filter((e) => showIdentical || e.state !== 'identical')
    .sort((a, b) => rank(a.state) - rank(b.state) || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name))
}

/** Counts per state over every file (the server's summary when present). */
export function planCounts(plan: PackagePlan): Record<PlanState | 'errors', number> {
  const out = { new: 0, changed: 0, identical: 0, kept: 0, skipped: 0, removed: 0, errors: 0 }
  if (plan.summary) return { ...out, ...plan.summary }
  for (const f of plan.files) {
    for (const e of f.entities ?? []) out[e.state] += 1
    out.errors += f.errors.length
  }
  return out
}

/** How many entities an apply would write (new + changed + removed). */
export function pendingChanges(plan: PackagePlan): number {
  const c = planCounts(plan)
  return c.new + c.changed + c.removed
}
