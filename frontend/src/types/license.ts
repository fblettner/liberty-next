// Shape of GET /api/license — see liberty/web/license.py / liberty/licensing.
export type LicenseMode = 'full' | 'restricted'

export interface LicenseInfo {
  mode: LicenseMode
  valid: boolean
  customer?: string
  email?: string
  plan?: string
  apps?: string[]       // the licensed applications this key covers (absent ⇒ every one) — what a plan sells
  expires_at?: number   // epoch seconds (absent ⇒ no expiry)
  error?: string        // why it's restricted (e.g. "License key expired"), when applicable
}

export const RESTRICTED: LicenseInfo = { mode: 'restricted', valid: false }
