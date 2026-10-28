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

/** Whether *user* holds *perm* — a superuser holds everything. */
export const can = (user: Pick<Principal, 'is_superuser' | 'permissions'> | null | undefined, perm: string): boolean =>
  !!user && (user.is_superuser || hasPermission(user.permissions ?? [], perm))

/** What the user may do in nomaflow (liberty/web/jobs.py): follow the jobs, edit them (which
 *  includes following), run one (`job:<id>`), and — superuser — the retention / missed policies. */
export function nomaflowAccess(user: Pick<Principal, 'is_superuser' | 'permissions'> | null | undefined) {
  const edit = can(user, 'nomaflow:edit')
  return {
    view: edit || can(user, 'nomaflow:view'),
    edit,
    run: (jobId: string) => can(user, `job:${jobId}`),
    policies: !!user?.is_superuser,
  }
}
