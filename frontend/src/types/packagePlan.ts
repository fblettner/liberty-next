// What `/admin/import-package` answers (a preview with `dry_run=true`, or the applied import): the
// plan of every entity of the package against this instance. Promotion to another instance
// returns the target's answer in the same shape.

export type PlanState = 'new' | 'changed' | 'identical' | 'kept' | 'skipped' | 'removed'

/** One changed field of an entity — ``from`` / ``to`` null when absent, secrets masked. */
export interface PlanDiff { key: string; from: string | null; to: string | null }

export interface PlanEntity {
  /** ``screen/<app>``, ``query/<connector>``, ``job``, ``pool``… (the scope after the slash). */
  kind: string
  name: string
  state: PlanState
  detail?: string
  diff?: PlanDiff[]
}

export interface PlanFile {
  file: string
  strategy: string
  added: string[]
  replaced: string[]
  skipped: string[]
  preserved_overrides?: string[]
  errors: string[]
  entities?: PlanEntity[]
}

export interface PackagePlan {
  files: PlanFile[]
  warnings: string[]
  reloaded: boolean
  dry_run?: boolean
  source?: { install?: string; version?: string | null }
  summary?: Partial<Record<PlanState | 'errors', number>>
}
