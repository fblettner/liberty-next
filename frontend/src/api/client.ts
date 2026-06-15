// Thin fetch wrapper: attaches the Bearer token + the UI language, parses JSON, and
// surfaces a typed error. On 401 it calls the registered onUnauthorized hook
// (auth/AuthContext wires it to "log out"). SSE is exposed separately via `streamSSE`.
import i18n from "../i18n";

let accessToken: string | null = null;
let onUnauthorized: (() => void) | null = null;
// Returns the new access token on success, or null if refresh failed. Wired by AuthContext to
// POST /auth/refresh. When set, a 401 triggers one silent refresh + retry before giving up.
let tokenRefresher: (() => Promise<string | null>) | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export function setTokenRefresher(fn: (() => Promise<string | null>) | null): void {
  tokenRefresher = fn;
}

// Single-flight: concurrent 401s share ONE refresh attempt instead of stampeding /auth/refresh.
function refreshOnce(): Promise<string | null> {
  if (!tokenRefresher) return Promise.resolve(null);
  refreshInFlight ??= tokenRefresher().finally(() => { refreshInFlight = null; });
  return refreshInFlight;
}

// Don't try to refresh the refresh/login calls themselves (would recurse / is meaningless).
function canRefresh(path: string): boolean {
  return !!tokenRefresher && !path.startsWith("/auth/refresh") && !path.startsWith("/auth/login");
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

/** Standard Bearer + ``X-Liberty-Lang`` headers used by every request. Exported so callers
 *  that need to hit ``fetch()`` directly (file downloads via blob — the ``api.*`` JSON
 *  helpers don't fit) can reuse the same auth shape. */
export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...(extra ?? {}) };
  if (accessToken) h["Authorization"] = `Bearer ${accessToken}`;
  // The server resolves query-result column labels in this language (the shared dictionary).
  const lang = (i18n.language || "").split("-")[0];
  if (lang) h["X-Liberty-Lang"] = lang;
  return h;
}

async function parseError(res: Response): Promise<ApiError> {
  let detail: unknown;
  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body?.detail ?? body;
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail)) message = detail.map((d: any) => d?.msg ?? JSON.stringify(d)).join("; ");
  } catch {
    /* non-JSON body */
  }
  return new ApiError(res.status, message, detail);
}

async function request<T>(method: string, path: string, body?: unknown, asText = false): Promise<T> {
  // Rebuilt per attempt so the retry picks up the refreshed Bearer (authHeaders reads the latest token).
  const build = (): RequestInit => {
    const init: RequestInit = { method, headers: authHeaders() };
    if (body !== undefined) {
      if (typeof body === "string") {
        init.body = body;
        (init.headers as Record<string, string>)["Content-Type"] = "text/plain";
      } else {
        init.body = JSON.stringify(body);
        (init.headers as Record<string, string>)["Content-Type"] = "application/json";
      }
    }
    return init;
  };
  let res = await fetch(path, build());
  if (res.status === 401 && canRefresh(path) && (await refreshOnce())) {
    res = await fetch(path, build());   // one silent retry with the refreshed token
  }
  if (res.status === 401) {
    onUnauthorized?.();
    throw await parseError(res);
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (asText ? await res.text() : await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  getText: (path: string) => request<string>("GET", path, undefined, true),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

/**
 * POST `path` with a JSON body and consume the Server-Sent-Events response,
 * invoking `onEvent` for each parsed `data:` JSON object. Returns when the
 * stream ends. Throws ApiError on a non-OK response (e.g. 401/403).
 */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (data: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const build = () => fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify(body),
    signal,
  });
  let res = await build();
  if (res.status === 401 && canRefresh(path) && (await refreshOnce())) res = await build();
  if (res.status === 401) {
    onUnauthorized?.();
    throw await parseError(res);
  }
  if (!res.ok || !res.body) throw await parseError(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    // SSE frames are separated by a blank line; here each frame is a single `data:` line.
    while ((nl = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, nl).trimEnd();
      buffer = buffer.slice(nl + 1);
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6);
      if (payload === "[DONE]") return;
      try {
        onEvent(JSON.parse(payload));
      } catch {
        /* ignore a malformed frame */
      }
    }
  }
}

/**
 * GET / POST `path` and consume an NDJSON (`application/x-ndjson`) response — one
 * JSON object per line, terminated by `\n`. Invokes `onEvent` for each parsed line.
 * Returns when the stream ends.
 *
 * Used by the SQL streaming endpoint (`/api/sql/{c}/{q}?_stream=1`) — the server
 * emits `{kind:"meta", ...}`, `{kind:"rows", ...}` repeatedly, then `{kind:"done", ...}`
 * (or a terminal `{kind:"error", detail:...}` if the query fails mid-stream).
 *
 * `signal` cancels the fetch; passing it lets the caller abort an in-flight load when
 * the user navigates away or kicks off a new query. The reader's `cancel()` releases
 * the underlying server-side cursor at the same time.
 *
 * Throws `ApiError` if the *initial* response is non-OK (e.g. 401/403/404/405). A
 * mid-stream failure does **not** throw — the server emits `{kind:"error", ...}` as
 * the last line, which `onEvent` sees like any other event.
 */
export async function streamNDJSON(
  path: string,
  init: { method?: "GET" | "POST"; body?: unknown; signal?: AbortSignal },
  onEvent: (data: unknown) => void,
): Promise<void> {
  const method = init.method ?? "GET";
  const build = () => fetch(path, {
    method,
    headers: authHeaders({
      Accept: "application/x-ndjson",
      ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
    }),
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init.signal,
  });
  let res = await build();
  if (res.status === 401 && canRefresh(path) && (await refreshOnce())) res = await build();
  if (res.status === 401) {
    onUnauthorized?.();
    throw await parseError(res);
  }
  if (!res.ok || !res.body) throw await parseError(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        try {
          onEvent(JSON.parse(line));
        } catch {
          /* ignore a malformed line */
        }
      }
    }
    // Final flush — the last chunk may not be `\n`-terminated.
    const tail = buffer.trim();
    if (tail) {
      try {
        onEvent(JSON.parse(tail));
      } catch {
        /* malformed terminal line */
      }
    }
  } finally {
    // Releases the server-side cursor + the fetch reader regardless of how we exit
    // (normal end, AbortSignal cancellation, parse error in the consumer's onEvent).
    reader.releaseLock();
  }
}
