// Auth shapes returned by liberty/auth (the `/auth/*` routes).

export interface Principal {
  id: string
  username: string
  email: string | null
  roles: string[]
  permissions: string[]
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
