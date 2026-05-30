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
  type: 'token' | 'thinking' | 'tool_call' | 'tool_result' | 'proposal' | 'error' | 'done'
  text?: string
  name?: string
  input?: Record<string, unknown>
  ok?: boolean
  summary?: string
  message?: string
  stop_reason?: string | null
  usage?: Record<string, number>
  /** Set on `proposal` events — the Proposal envelope emitted by a scaffold tool. */
  data?: Proposal
}

/** What a scaffold tool proposes — rendered as an Apply card in the chat. The kinds match
 *  liberty.ai.proposal.ProposalKind; the apply endpoint dispatches by `kind`. */
export interface Proposal {
  kind: 'dictionary_entries' | 'connector_queries' | 'screen' | 'menu_item'
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
}
