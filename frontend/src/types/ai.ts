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
  type: 'token' | 'thinking' | 'tool_call' | 'tool_result' | 'error' | 'done'
  text?: string
  name?: string
  input?: Record<string, unknown>
  ok?: boolean
  summary?: string
  message?: string
  stop_reason?: string | null
  usage?: Record<string, number>
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}
