import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, setAccessToken, setTokenRefresher, setUnauthorizedHandler } from "../api/client";
import type { Principal, TokenPair } from "../types/auth";

const STORAGE_KEY = "liberty.tokens";
// Refresh this long BEFORE the access token's exp so a renewed token is in hand before any call
// needs it (also absorbs clock skew). Backend access TTL is 1 h; refresh TTL 14 d.
const REFRESH_SKEW_MS = 60_000;
const DEFAULT_TTL_S = 3600;

interface StoredTokens {
  access_token: string;
  refresh_token: string;
  expires_at?: number; // epoch ms when the access token expires
}

interface TokenInput {
  access_token: string;
  refresh_token: string;
  expires_in?: number; // seconds; defaults to DEFAULT_TTL_S (e.g. OIDC hash carries no expires_in)
}

function load(): StoredTokens | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredTokens) : null;
  } catch {
    return null;
  }
}

function save(t: StoredTokens | null): void {
  if (t) localStorage.setItem(STORAGE_KEY, JSON.stringify(t));
  else localStorage.removeItem(STORAGE_KEY);
}

// Raw fetch (NOT the api client) so a 401 here can't recurse into the refresh-and-retry path.
async function postRefresh(refreshToken: string): Promise<TokenPair | null> {
  try {
    const res = await fetch("/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return null;
    return (await res.json()) as TokenPair;
  } catch {
    return null;
  }
}

interface AuthState {
  user: Principal | null;
  ready: boolean; // initial token-check finished
  login: (username: string, password: string) => Promise<void>;
  setTokens: (pair: TokenInput) => Promise<void>;
  oidcLogin: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Principal | null>(null);
  const [ready, setReady] = useState(false);
  const timerRef = useRef<number | null>(null);
  // Indirection so the api client's refresher always calls the latest doRefresh closure.
  const doRefreshRef = useRef<() => Promise<string | null>>(async () => null);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const logout = useCallback(() => {
    clearTimer();
    save(null);
    setAccessToken(null);
    setUser(null);
  }, [clearTimer]);

  // Schedule a silent proactive refresh shortly before the access token expires.
  const scheduleRefresh = useCallback((expiresAt?: number) => {
    clearTimer();
    if (!expiresAt) return;
    const delay = Math.max(5_000, expiresAt - Date.now() - REFRESH_SKEW_MS);
    timerRef.current = window.setTimeout(() => { void doRefreshRef.current(); }, delay);
  }, [clearTimer]);

  const persist = useCallback((pair: TokenInput): number => {
    const expires_at = Date.now() + (pair.expires_in ?? DEFAULT_TTL_S) * 1000;
    save({ access_token: pair.access_token, refresh_token: pair.refresh_token, expires_at });
    setAccessToken(pair.access_token);
    scheduleRefresh(expires_at);
    return expires_at;
  }, [scheduleRefresh]);

  // Exchange the stored refresh token for a fresh pair. Returns the new access token, or null
  // (and logs out) on failure. Used both proactively (timer) and reactively (api client on 401).
  const doRefresh = useCallback(async (): Promise<string | null> => {
    const stored = load();
    if (!stored?.refresh_token) {
      logout();
      return null;
    }
    const pair = await postRefresh(stored.refresh_token);
    if (!pair) {
      logout();
      return null;
    }
    persist(pair);
    return pair.access_token;
  }, [logout, persist]);

  useEffect(() => { doRefreshRef.current = doRefresh; }, [doRefresh]);

  const applyTokens = useCallback(async (pair: TokenInput) => {
    persist(pair);
    const me = await api.get<Principal>("/auth/me");
    setUser(me);
  }, [persist]);

  // On mount: wire the client hooks, then validate the stored token (auto-refreshing if it has
  // already expired but the refresh token is still valid — e.g. after the laptop slept).
  useEffect(() => {
    setUnauthorizedHandler(logout);
    setTokenRefresher(() => doRefreshRef.current());
    const stored = load();
    if (!stored) {
      setReady(true);
      return;
    }
    setAccessToken(stored.access_token);
    scheduleRefresh(stored.expires_at);
    api
      .get<Principal>("/auth/me")
      .then(setUser)
      .catch(() => logout())
      .finally(() => setReady(true));
    return () => {
      clearTimer();
      setTokenRefresher(null);
    };
  }, [logout, scheduleRefresh, clearTimer]);

  const login = useCallback(
    async (username: string, password: string) => {
      const pair = await api.post<TokenPair>("/auth/login", { username, password });
      await applyTokens(pair);
    },
    [applyTokens],
  );

  const oidcLogin = useCallback(() => {
    window.location.href = "/auth/oidc/login";
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, ready, login, setTokens: applyTokens, oidcLogin, logout }),
    [user, ready, login, applyTokens, oidcLogin, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
