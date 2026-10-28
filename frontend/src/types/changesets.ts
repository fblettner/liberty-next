// A change bundle's apply report (``POST /admin/changesets/apply``, here or on a promotion target).
export type BundleOp = { connector: string; query: string; operation: string; entity: string | null; entity_key: Record<string, unknown> | null; post_apply?: boolean; label?: string | null }
export type BundleResult = { index: number; op: BundleOp; status: string; detail?: string; rowcount?: number }
export type AlreadyApplied = { name: string; applied_at: string | null; applied_by: string | null }
export type BundleReport = { dry_run: boolean; op_count: number; summary: Record<string, number>; results: BundleResult[]; already_applied?: AlreadyApplied | null }
