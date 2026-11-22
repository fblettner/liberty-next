// Auth shapes returned by liberty/auth (the `/auth/*` routes).

export interface Principal {
  id: string
  username: string
  email: string | null
  roles: string[]
  permissions: string[]
  /** What the user's menu grants give on top of `permissions` (a role allows a menu folder, the server
   *  works out what its screens need) — `GET /auth/me`. Absent from an older server. */
  implied?: string[]
  is_superuser: boolean
  provider: string
  /** Signed in with a temporary password: a new one is required before anything else. */
  must_change_password?: boolean
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}
