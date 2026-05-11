import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setAccessToken, setUnauthorizedHandler } from "../api/client";
import type { Principal, TokenPair } from "../types/auth";

const STORAGE_KEY = "liberty.tokens";

interface StoredTokens {
  access_token: string;
  refresh_token: string;
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

interface AuthState {
  user: Principal | null;
  ready: boolean; // initial token-check finished
  login: (username: string, password: string) => Promise<void>;
  setTokens: (pair: { access_token: string; refresh_token: string }) => Promise<void>;
  oidcLogin: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Principal | null>(null);
  const [ready, setReady] = useState(false);

  const applyTokens = useCallback(async (pair: StoredTokens) => {
    save(pair);
    setAccessToken(pair.access_token);
    const me = await api.get<Principal>("/auth/me");
    setUser(me);
  }, []);

  const logout = useCallback(() => {
    save(null);
    setAccessToken(null);
    setUser(null);
  }, []);

  // On mount: if we have a stored token, validate it via /auth/me.
  useEffect(() => {
    setUnauthorizedHandler(logout);
    const stored = load();
    if (!stored) {
      setReady(true);
      return;
    }
    setAccessToken(stored.access_token);
    api
      .get<Principal>("/auth/me")
      .then(setUser)
      .catch(() => logout())
      .finally(() => setReady(true));
  }, [logout]);

  const login = useCallback(
    async (username: string, password: string) => {
      const pair = await api.post<TokenPair>("/auth/login", { username, password });
      await applyTokens({ access_token: pair.access_token, refresh_token: pair.refresh_token });
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
