/**
 * Conversation token usage (`/_local/usage`).
 *
 * This is a **dashboard-local** read, not a Core API surface. Core records what an
 * Agent *said* (`events.payload_json`) and deliberately records no billing metadata,
 * so token usage can only come from the Agent's own session files on this machine.
 * `server/conversation-usage.mjs` parses those files and this module is the single
 * client contract for the result.
 *
 * Rules this file exists to keep:
 *
 * - Everything is read-only. The endpoint never writes, never runs an Agent, and
 *   never accepts a filesystem path from the browser: the server decides which
 *   session roots it may read.
 * - A missing or unreadable source is data, not an error: `sources[].status` says
 *   `missing`/`error` and the UI must show that gap instead of an empty total that
 *   looks like "you spent nothing".
 * - A count the source cannot report is `0`; only a genuinely unknown monetary cost
 *   is `null`. `cache_hit_ratio` is `null` when nothing was billable.
 * - No prompt text is ever part of this payload.
 */

export const USAGE_SCHEMA = 'jasmine-core/conversation-usage@1' as const

/** Capture adapters use these ids; the endpoint reports the same vocabulary. */
export type UsageAgent = 'claude' | 'codex' | 'dsh' | 'grok' | 'antigravity' | 'opencode'

export const USAGE_AGENTS: UsageAgent[] = ['claude', 'codex', 'dsh', 'grok', 'antigravity', 'opencode']

export const USAGE_AGENT_LABELS: Record<UsageAgent, string> = {
  claude: 'Claude Code',
  codex: 'Codex',
  dsh: 'DSH',
  grok: 'Grok',
  antigravity: 'Antigravity',
  opencode: 'OpenCode',
}

export interface UsageTokens {
  input_tokens: number
  cached_read_tokens: number
  cache_write_tokens: number
  output_tokens: number
  reasoning_tokens: number
  total_tokens: number
  cost_usd: number | null
}

export interface UsageTotals extends UsageTokens {
  sessions: number
  turns: number
  cache_hit_ratio: number | null
}

export interface UsageAgentRow extends UsageTotals {
  agent: UsageAgent
}

export interface UsageModelRow extends UsageTotals {
  agent: UsageAgent
  model: string
}

export interface UsageDayRow extends UsageTokens {
  /** Local calendar day, `YYYY-MM-DD`, in this machine's time zone. */
  day: string
  sessions: number
  turns: number
}

export interface UsageSessionRow extends UsageTokens {
  agent: UsageAgent
  native_session_id: string
  /** Working directory the Agent ran in, as the session file recorded it. */
  project: string | null
  models: string[]
  started_at: string | null
  last_activity_at: string | null
  turns: number
}

export interface UsageSourceStatus {
  agent: UsageAgent
  /** Home-relative root that was read, e.g. `~/.claude/projects`. */
  root: string
  status: 'ok' | 'missing' | 'error'
  sessions: number
  /** Why a root is `missing`/`error`; absent when `ok`. */
  detail?: string
}

export interface UsageTurn extends UsageTokens {
  /** 1-based position inside the session, in file order. */
  index: number
  /** Native turn identity when the source has one (Codex `turn_id`, DSH turn id). */
  native_turn_id: string | null
  /** DSH reports turn and step; other sources leave this `null`. */
  step: number | null
  at: string | null
  model: string | null
}

export interface UsageInventory {
  schema: typeof USAGE_SCHEMA
  generated_at: string
  totals: UsageTotals
  by_agent: UsageAgentRow[]
  by_model: UsageModelRow[]
  by_day: UsageDayRow[]
  sessions: UsageSessionRow[]
  sources: UsageSourceStatus[]
  warnings: string[]
}

export interface UsageSessionDetail {
  schema: typeof USAGE_SCHEMA
  agent: UsageAgent
  native_session_id: string
  project: string | null
  models: string[]
  started_at: string | null
  last_activity_at: string | null
  totals: UsageTotals
  turns: UsageTurn[]
  warnings: string[]
}

/** The endpoint's own envelope for a refused or failed read. */
export interface UsageErrorBody {
  error: string
  detail?: string
}

export interface UsageFetchOptions {
  signal?: AbortSignal
  /** Bearer token from the local sign-in; the endpoint authorizes it against Core. */
  token?: string
  /** Test seam only; replaced in unit tests, never in the browser. */
  fetchImpl?: typeof fetch
}

function usageQuery(query: Record<string, string | undefined> | undefined): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

async function readUsageJson<T>(path: string, options: UsageFetchOptions): Promise<T> {
  const fetchImpl = options.fetchImpl ?? fetch
  const headers: Record<string, string> = {}
  if (options.token) headers.Authorization = `Bearer ${options.token}`
  const response = await fetchImpl(path, {
    headers,
    signal: options.signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    let detail: string | undefined
    try {
      const body = (await response.json()) as UsageErrorBody
      detail = body.error
    } catch {
      detail = undefined
    }
    const error = new Error(`usage_unavailable:${response.status}`)
    if (detail) (error as Error & { detail?: string }).detail = detail
    throw error
  }
  return (await response.json()) as T
}

export interface UsageSessionQuery {
  agent: UsageAgent
  nativeSessionId: string
}

/** All sessions this machine has usage for, with totals and breakdowns. */
export function fetchUsageInventory(options: UsageFetchOptions = {}): Promise<UsageInventory> {
  return readUsageJson<UsageInventory>(`/_local/usage${usageQuery({})}`, options)
}

/** One native session's per-turn series. */
export function fetchUsageSession(
  query: UsageSessionQuery,
  options: UsageFetchOptions = {},
): Promise<UsageSessionDetail> {
  return readUsageJson<UsageSessionDetail>(
    `/_local/usage/session${usageQuery({
      agent: query.agent,
      native_session_id: query.nativeSessionId,
    })}`,
    options,
  )
}

/** Every hand-built URL in this module is same-origin and path-only. */
export const USAGE_PATHS = {
  inventory: '/_local/usage',
  session: '/_local/usage/session',
} as const
