// AI assistant shapes — `GET /ai/tools` and the `POST /ai/chat` SSE stream.

export interface AiTool {
  name: string
  description?: string
  type?: string
  input_schema?: unknown
  allowed_domains?: string[]
}

/** One JSON object per `data:` line on the `/ai/chat` SSE stream. */
export interface ChatEvent {
  type: 'token' | 'thinking' | 'tool_call' | 'tool_result' | 'proposal' | 'followups' | 'error' | 'done'
  text?: string
  name?: string
  input?: Record<string, unknown>
  ok?: boolean
  summary?: string
  message?: string
  stop_reason?: string | null
  usage?: Record<string, number>
  /** Set on `proposal` events — the Proposal envelope emitted by a scaffold tool — and on
   *  `followups` events (`{questions: string[]}`). */
  data?: Proposal | { questions: string[] }
}

/** What a scaffold tool proposes — rendered as an Apply card in the chat. The kinds match
 *  liberty.ai.proposal.ProposalKind; the apply endpoint dispatches by `kind`. */
export interface Proposal {
  kind: 'dictionary_entries' | 'connector_queries' | 'screen' | 'menu_item' | 'note' | 'screen_write' | 'screen_write_many' | 'screen_action' | 'job_run' | 'api_run'
  summary: string
  target_path: string
  payload: Record<string, unknown>
  app?: string
  connector?: string
  notes?: string[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  /** The names of the files attached to a question (kept with the conversation). */
  attachments?: string[]
  /** The files themselves, as the parts the model reads (`POST /ai/attachments`): in memory only,
   *  for the questions of the open conversation — never stored. */
  blocks?: unknown[]
}

/** `POST /ai/attachments`: a file prepared for a question. */
export interface Attachment { name: string; kind: 'image' | 'pdf' | 'table' | 'text'; size: number; block: unknown; note?: string }

/** A conversation kept for the user (the saved-views store, kind `chat`): what they saw of it. */
export interface Conversation { id: string; title: string; app: string | null; updated_at: string | null; messages: ChatMessage[] }
/** A note the user accepted (kind `ai_note`); `app` is the application it is about, or `*`. */
export interface AiNote { id: string; app: string; text: string }

export interface AiModel { id: string; label: string }

/** `GET /ai/tools`. */
export interface AiInfo {
  available: boolean
  model: string
  tools: AiTool[]
  /** The models the caller may pick (the configured one first); one entry = no picker. */
  models: AiModel[]
  /** Starter questions by scope: `*`, an app, or `app/screen`. */
  starters: Record<string, string[]>
  /** The same keys with the questions under headings (ai.toml `groups`); shown as pills when a key has them. */
  starter_groups?: Record<string, StarterGroup[]>
  /** Files attached to a question: the size limit (0 = off) and the extensions the picker offers. */
  attachments?: { max_mb: number; accept: string[] }
  /** The installation keeps the data it flagged sensitive from the assistant: the page context leaves those columns out. */
  hide_sensitive?: boolean
}

export interface StarterGroup { label: string | null; starters: string[] }
