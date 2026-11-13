/**
 * Types for `conversation-usage.mjs`.
 *
 * The scanner is plain ESM JavaScript so a dashboard run never needs a build step; these
 * declarations exist so the TypeScript tests (and `tsc --noEmit`) can import it without an
 * implicit `any`. The JSON shapes come from the frozen contract in `src/api/usage.ts`, not
 * from a second definition here, so a change to the contract fails typecheck instead of
 * drifting.
 */
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { UsageAgent, UsageInventory, UsageSessionDetail } from '../src/api/usage'

/**
 * Result of an external helper (`zstd`, `sqlite3`) in the shape the scanners consume.
 *
 * `ok` is true for a truncated frame that still produced output: a partial read is a
 * success with `truncated: true`, and the caller records a warning rather than failing the
 * whole source.
 */
export interface UsageCommandResult {
  ok: boolean
  /** Exit code, or null when the process never started. */
  code: number | null
  stdout: string
  stderr: string
  /** Output stopped at `maxBytes` or at the forced kill. */
  truncated: boolean
  error?: NodeJS.ErrnoException
}

export type UsageCommandRunner = (
  command: string,
  args: string[],
  options?: { timeoutMs?: number; maxBytes?: number },
) => Promise<UsageCommandResult>

export interface UsageLimits {
  /** How long an inventory may be served without rescanning. Sessions are still re-checked
   *  by mtime, so an expired TTL costs a stat per file rather than a reparse. */
  cacheTtlMs?: number
  /** Newest-N cap per agent, applied after discovery sorts by mtime. */
  maxSessionsPerAgent?: number
  /** Cap on the per-turn series a detail read returns. */
  maxDetailTurns?: number
  /** Budget for one `zstd -dc` of a DSH session. */
  zstdTimeoutMs?: number
  /** Budget for the read-only `sqlite3` queries behind OpenCode and Antigravity. */
  sqliteTimeoutMs?: number
}

/** Every root is an absolute directory except `opencode`, which is its database file. */
export interface UsageRoots {
  claude?: string
  codex?: string
  codexArchived?: string
  dsh?: string
  grok?: string
  /** One home per Antigravity front-end (app, CLI, IDE); all of them write transcripts. */
  antigravity?: string | string[]
  antigravitySummaries?: string | string[]
  opencode?: string
}

export interface UsageServiceOptions {
  home?: string
  now?: () => number
  roots?: UsageRoots
  limits?: UsageLimits
  run?: UsageCommandRunner
}

export interface UsageService {
  readonly agents: readonly UsageAgent[]
  readUsageInventory(): Promise<UsageInventory>
  readUsageSession(agent: string, nativeSessionId: string): Promise<UsageSessionDetail | null>
  invalidate(): void
}

export const USAGE_SCHEMA: string
export const USAGE_AGENTS: UsageAgent[]
export const USAGE_NATIVE_SESSION_ID: RegExp

export function createUsageService(options?: UsageServiceOptions): UsageService

/** True for the two paths this module answers, so a host can route them in one test. */
export function isUsagePath(pathname: string): boolean

export function serveLocalUsage(
  req: IncomingMessage,
  res: ServerResponse,
  origin: URL,
  service: UsageService,
): Promise<void>
