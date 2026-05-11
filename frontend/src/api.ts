// Thin fetch wrapper: attaches the Bearer token, parses JSON, and surfaces a
// typed error. On 401 it calls the registered onUnauthorized hook (auth.tsx wires
// it to "log out"). SSE is exposed separately via `streamSSE`.

let accessToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...(extra ?? {}) };
  if (accessToken) h["Authorization"] = `Bearer ${accessToken}`;
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
  const res = await fetch(path, init);
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
  const res = await fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify(body),
    signal,
  });
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
