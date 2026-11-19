// The AI usage page's calls and plain helpers (no React): the assistant's audit and usage
// record — who, model, tokens, tools; never the question, the answer or a row.
import { api } from '../api/client'

export interface UsageFigures {
  turns: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  tool_calls: number
  tool_errors: number
  errors: number
  duration_ms: number
  /** Estimated at list price, in USD — null when the group holds a model without a listed price. */
  cost?: number | null
}
/** A change made through the assistant: a card the user applied, or a saving tool called over MCP. */
export interface Change { at: string | null; username: string; channel: string; app: string | null; tool: string; what: string; ok: boolean | null; error?: string }
export interface ToolFigures { name: string; calls: number; errors: number; ms: number; chars: number }
export interface TurnTool { name: string; input: string; ok: boolean | null; ms?: number; chars?: number; error?: string }
export interface Turn {
  id: string
  at: string | null
  username: string
  channel: string
  app: string | null
  model: string
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  tool_calls: number
  tool_errors: number
  duration_ms: number
  stop_reason: string | null
  error: string | null
  tools: TurnTool[]
}
export interface UsageSummary {
  days: number
  totals: UsageFigures
  by_user: (UsageFigures & { username: string })[]
  by_day: (UsageFigures & { day: string })[]
  by_model: (UsageFigures & { model: string })[]
  by_channel?: (UsageFigures & { channel: string })[]
  by_tool: ToolFigures[]
  changes?: Change[]
  /** The models of the period that have no listed price: their cost is not in the estimate. */
  unpriced?: string[]
}
export interface UsageResponse { summary: UsageSummary; recent: Turn[]; retention_days: number }

export function getUsage(days: number, username?: string | null, channel?: string | null, failed?: boolean): Promise<UsageResponse> {
  const p = new URLSearchParams({ days: String(days) })
  if (username) p.set('username', username)
  if (channel) p.set('channel', channel)
  if (failed) p.set('failed', 'true')
  return api.get<UsageResponse>(`/admin/ai-usage?${p.toString()}`)
}

type Tokens = Pick<UsageFigures, 'input_tokens' | 'cache_read_tokens' | 'cache_creation_tokens'>

/** Everything the model read: the fresh input plus what was read from / written to the cache. */
export function tokensIn(f: Tokens): number {
  return f.input_tokens + f.cache_read_tokens + f.cache_creation_tokens
}

/** The share of the input read from the prompt cache, 0–100 (billed at a fraction of the price). */
export function cachedShare(f: Tokens): number {
  const total = tokensIn(f)
  return total > 0 ? Math.round((100 * f.cache_read_tokens) / total) : 0
}

/** A token count in short: 950, 12.4 k, 1.2 M. */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)} k`
  return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 2 : 1)} M`
}

/** A duration in short: 850 ms, 4.2 s, 1 min 05 s. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)} s`
  return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, '0')} s`
}

/** The mean time of a turn (or of a tool call) over a group. */
export function average(total: number, count: number): number {
  return count > 0 ? total / count : 0
}

/** An estimated cost in USD: $0.004, $1.25, $312 — a dash when it cannot be estimated. */
export function formatCost(cost: number | null | undefined): string {
  if (cost == null) return '—'
  if (cost === 0) return '$0'
  if (cost < 0.01) return '$' + cost.toFixed(3)
  if (cost < 100) return '$' + cost.toFixed(2)
  return '$' + String(Math.round(cost))
}

/** One line of the latest activity: a turn, or a run of MCP tool calls shown as one. */
export type Activity = { kind: 'turn'; turn: Turn } | { kind: 'mcp'; id: string; turns: Turn[] }

/** The seconds between two MCP calls of the same user above which they are two runs. */
export const MCP_GAP_SECONDS = 120

/** Over MCP every tool call is recorded on its own: a question to Claude that took ten calls
 *  filled the list with ten lines. Consecutive MCP calls of one user less than MCP_GAP_SECONDS
 *  apart are one line here (*turns* newest first, as the server gives them). */
export function groupActivity(turns: readonly Turn[]): Activity[] {
  const out: Activity[] = []
  for (const turn of turns) {
    const last = out[out.length - 1]
    if (turn.channel === 'mcp' && last?.kind === 'mcp') {
      const prev = last.turns[last.turns.length - 1]
      const gap = prev.at && turn.at ? Math.abs(new Date(prev.at).getTime() - new Date(turn.at).getTime()) / 1000 : Infinity
      if (prev.username === turn.username && gap <= MCP_GAP_SECONDS) { last.turns.push(turn); continue }
    }
    out.push(turn.channel === 'mcp' ? { kind: 'mcp', id: turn.id, turns: [turn] } : { kind: 'turn', turn })
  }
  return out
}
