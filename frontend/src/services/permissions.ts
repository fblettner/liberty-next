// The backend's permission matcher (Principal.has_permission / is_denied), for the screens that
// show or hide an action by what the signed-in user may do — the server checks it again.
import type { Principal } from '../types/auth'

/** Colon-segment glob: `*` matches one segment; a trailing `*` matches the rest. */
export function segmentMatch(pattern: string, value: string): boolean {
  const ps = pattern.split(':')
  const vs = value.split(':')
  for (let i = 0; i < ps.length; i++) {
    if (i >= vs.length) return false
    if (ps[i] === '*') {
      if (i === ps.length - 1) return true
      continue
    }
    if (ps[i] !== vs[i]) return false
  }
  return ps.length === vs.length
}
export const isDenied = (perms: string[], perm: string): boolean => perms.some((p) => p.startsWith('!') && segmentMatch(p.slice(1), perm))
export const hasPermission = (perms: string[], perm: string): boolean =>
  !isDenied(perms, perm) && perms.some((p) => !p.startsWith('!') && segmentMatch(p, perm))

type Holder = Pick<Principal, 'is_superuser' | 'permissions'> & Pick<Partial<Principal>, 'implied'>

/** Whether *user* may do *perm* — the check the server makes: a superuser may everything; else the
 *  rules held by name (a deny wins); else what the menu grants give (`implied`: a role allows a
 *  menu folder, the server works out what its screens need and says it at sign-in). */
export const can = (user: Holder | null | undefined, perm: string): boolean => {
  if (!user) return false
  if (user.is_superuser) return true
  const perms = user.permissions ?? []
  if (isDenied(perms, perm)) return false
  return hasPermission(perms, perm) || (user.implied ?? []).includes(perm)
}

/** What the user may do in nomaflow (liberty/web/jobs.py): follow the jobs, edit them (which
 *  includes following), run one (`job:<id>`), and — superuser — the retention / missed policies. */
export function nomaflowAccess(user: Holder | null | undefined) {
  const edit = can(user, 'nomaflow:edit')
  return {
    view: edit || can(user, 'nomaflow:view'),
    edit,
    run: (jobId: string) => can(user, `job:${jobId}`),
    policies: !!user?.is_superuser,
  }
}
