// Wire shapes of /api/notifications (liberty/web/notifications.py) and notifications.toml
// (liberty/notify/rules.py) as the Settings editor sees it.

export interface Notification {
  id: string
  username: string
  rule: string | null
  subject: string
  body: string | null
  /** A frontend route the row opens (e.g. /screen/nomajde/period_status), or null. */
  link: string | null
  payload: Record<string, unknown>
  created_at: string | null
  read: boolean
  read_at: string | null
}

export type NotificationChannel = 'portal' | 'email' | 'action'
export type ScreenEvent = 'insert' | 'update' | 'delete'
export type JobEvent = 'failure' | 'succeeded' | 'canceled' | 'missed' | 'long_run' | 'late'

export interface RuleWhen { field: string; value: string | string[] }

export type RuleTrigger =
  | { kind: 'screen'; app: string; screen: string; events?: ScreenEvent[]; when?: RuleWhen[] }
  | { kind: 'job'; events?: JobEvent[]; jobs?: string[]; long_run_minutes?: number | null }
  | { kind: 'action' }

export interface RuleActionTarget {
  connector: string
  query?: string | null
  endpoint?: string | null
  params?: Record<string, unknown>
}

export interface NotificationRule {
  label?: string | null
  enabled?: boolean
  trigger: RuleTrigger
  channels?: NotificationChannel[]
  recipients?: string[]
  subject: string
  body?: string | null
  link?: string | null
  action?: RuleActionTarget | null
  override?: boolean
}

export interface DispatchReportWire {
  portal_users: string[]
  emails: string[]
  action_ran: boolean
  errors: Record<string, string>
}
