// Shapes returned by the Liberty v2 backend (see liberty/web, liberty/auth, liberty/ai).

export interface ParamDef {
  name: string;
  label: string | null;
  default: string | null;
}

export interface SqlQueryMeta {
  name: string;
  label: string | null;
  description: string | null;
  writable: boolean;
  statement_type: string;
  params: ParamDef[];
  bind_params: string[];
}

export interface ApiEndpointMeta {
  name: string;
  label: string | null;
  description: string | null;
  method: string;
  path: string;
  params: ParamDef[];
}

export interface SqlConnectorMeta {
  name: string;
  type: "sql";
  queries: SqlQueryMeta[];
}

export interface ApiConnectorMeta {
  name: string;
  type: "api";
  base_url: string | null;
  auth_type: string | null;
  endpoints: ApiEndpointMeta[];
}

export type ConnectorMeta = SqlConnectorMeta | ApiConnectorMeta;

export interface Column {
  name: string;
  type: string | null;
}

export interface QueryResult {
  connector: string;
  query: string;
  statement_type: string;
  columns: Column[];
  rows: Record<string, unknown>[];
  row_count: number;
  rowcount: number;
  truncated: boolean;
  duration_ms: number;
}

export interface ApiResult {
  connector: string;
  endpoint: string;
  success: boolean;
  status_code: number;
  url: string;
  json: unknown;
  body: string | null;
  extracted: unknown;
  mapped: Record<string, unknown>;
  error: string | null;
  duration_ms: number;
}

export interface Principal {
  id: string;
  username: string;
  email: string | null;
  roles: string[];
  permissions: string[];
  is_superuser: boolean;
  provider: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AiTool {
  name: string;
  description?: string;
  type?: string;
  input_schema?: unknown;
  allowed_domains?: string[];
}

// Streamed by POST /ai/chat (SSE), one JSON object per `data:` line.
export interface ChatEvent {
  type: "token" | "thinking" | "tool_call" | "tool_result" | "error" | "done";
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
  message?: string;
  stop_reason?: string | null;
  usage?: Record<string, number>;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}
