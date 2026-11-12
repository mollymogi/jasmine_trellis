/**
 * Conversation token-usage helpers.
 *
 * The reader can only ask about a native session when the conversation states one
 * unambiguously, and it may only attach a number to a message when the turn is
 * actually identified. Both rules are data decisions, so they live here instead of
 * inside the components:
 *
 * - Identity comes from the events the browser already loaded
 *   (`payload_json.native_session_id`) plus the capture producer
 *   (`source_system`). Two disagreeing identities in one conversation are treated
 *   as unknown rather than merged, because a merged request would describe a
 *   different session.
 * - A turn match is `native` when both sides carry the same `native_turn_id`, and
 *   `ordinal` only when *neither* side identifies turns and the counts line up
 *   (Grok never records a turn id). Everything else is unmatched: showing nothing
 *   is honest, showing a wrong or zero count is not.
 */

import type { UsageAgent, UsageTurn } from '../api/usage'
import type { RawEvent } from '../api/types'

export interface UsageTarget {
  agent: UsageAgent
  nativeSessionId: string
}

/** Same producer vocabulary as `src/lib/agentIdentity.ts`, narrowed to billable Agents. */
const AGENT_BY_SOURCE: ReadonlyArray<readonly [RegExp, UsageAgent]> = [
  [/^claude(?:$|[-_:./])/, 'claude'],
  [/^codex(?:$|[-_:./])/, 'codex'],
  [/^dsh(?:$|[-_:./])/, 'dsh'],
  [/^grok(?:$|[-_:./])/, 'grok'],
  [/^antigravity(?:$|[-_:./])/, 'antigravity'],
  [/^opencode(?:$|[-_:./])/, 'opencode'],
]

/** The Agent whose session file would describe these events, or `null` when the
 * producer is not a capture adapter this dashboard can price. */
export function usageAgentFromSource(sourceSystem: string): UsageAgent | null {
  const producer = sourceSystem.trim().toLowerCase()
  for (const [pattern, agent] of AGENT_BY_SOURCE) {
    if (pattern.test(producer)) return agent
  }
  return null
}

/** The native session to ask about, or `null` when the conversation does not name
 * exactly one. Messages without a native id never contribute one; a conversation
 * whose (producer, id) pairs disagree is deliberately unresolved. */
export function deriveUsageTarget(events: RawEvent[]): UsageTarget | null {
  let target: UsageTarget | null = null
  for (const event of events) {
    const nativeSessionId = event.payload['native_session_id']
    if (typeof nativeSessionId !== 'string' || nativeSessionId.trim() === '') continue
    const agent = usageAgentFromSource(event.source_system)
    if (!agent) continue
    if (target === null) {
      target = { agent, nativeSessionId }
      continue
    }
    if (target.agent !== agent || target.nativeSessionId !== nativeSessionId) return null
  }
  return target
}

/** How a message was paired with a usage turn. `ordinal` is an approximation and
 * the UI says so. */
export interface MatchedUsageTurn {
  turn: UsageTurn
  mode: 'native' | 'ordinal'
}

/** Pair assistant messages with their usage turn, keyed by `event_id`.
 *
 * Rows that cannot be paired with certainty are simply absent from the map; the
 * caller renders nothing for them. A message whose `native_turn_id` exists but is
 * not in the usage series is *not* given a positional guess: the source named a
 * turn, so position would be a different turn.
 */
export function matchUsageTurns(
  messages: RawEvent[],
  turns: UsageTurn[] | null | undefined,
): Map<string, MatchedUsageTurn> {
  const matched = new Map<string, MatchedUsageTurn>()
  if (!turns || turns.length === 0) return matched
  const byNativeId = new Map<string, UsageTurn>()
  for (const turn of turns) {
    if (turn.native_turn_id) byNativeId.set(turn.native_turn_id, turn)
  }
  const assistants = messages.filter(event => event.event_type === 'assistant.message')
  const unidentified: RawEvent[] = []
  for (const message of assistants) {
    const nativeTurnId = message.payload['native_turn_id']
    if (typeof nativeTurnId === 'string' && nativeTurnId !== '') {
      const turn = byNativeId.get(nativeTurnId)
      if (turn) matched.set(message.event_id, { turn, mode: 'native' })
      continue
    }
    unidentified.push(message)
  }
  // Positional matching is only defensible when the source identifiers are absent
  // everywhere (Grok) and both lists describe the same turns. A partial identifier
  // or a count mismatch means the file recorded more/fewer turns than Core, so
  // position no longer implies the same turn.
  if (unidentified.length === 0 || unidentified.length !== assistants.length) return matched
  if (turns.some(turn => turn.native_turn_id !== null)) return matched
  if (turns.length !== assistants.length) return matched
  const ordered = [...turns].sort((left, right) => left.index - right.index)
  unidentified.forEach((message, position) => {
    const turn = ordered[position]
    if (turn) matched.set(message.event_id, { turn, mode: 'ordinal' })
  })
  return matched
}

/** Compact token count: `987`, `1.2k`, `3.4M`. */
export function formatTokenCount(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0'
  if (value >= 999_950) return `${trimCount(value / 1_000_000)}M`
  if (value >= 1_000) return `${trimCount(value / 1_000)}k`
  return String(Math.round(value))
}

function trimCount(value: number): string {
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

/** USD amount as reported; a sub-dollar session keeps cent-level precision and
 * trailing zeros are trimmed, but at least two decimals are always shown. `null`
 * cost is never formatted (the caller hides it). */
export function formatCostUsd(value: number): string {
  const fixed = value > 0 && value < 1 ? value.toFixed(4) : value.toFixed(2)
  const trimmed = fixed.replace(/(\.\d\d)0+$/, '$1')
  return `$${trimmed}`
}

/** `cache_hit_ratio` is a fraction; tolerate a source that reports percent. */
export function formatCacheHitRatio(ratio: number): string {
  const percent = ratio > 0 && ratio <= 1 ? ratio * 100 : ratio
  const rounded = percent >= 10 ? Math.round(percent) : Math.round(percent * 10) / 10
  return `${rounded}%`
}

/** Tooltip for one badge: the breakdown behind `total_tokens`, plus a visible
 * disclaimer for the positional match. */
export function usageTurnTitle(match: MatchedUsageTurn): string {
  const { turn } = match
  const parts = [
    `输入 ${formatTokenCount(turn.input_tokens)}`,
    `输出 ${formatTokenCount(turn.output_tokens)}`,
    `缓存读 ${formatTokenCount(turn.cached_read_tokens)}`,
    `缓存写 ${formatTokenCount(turn.cache_write_tokens)}`,
  ]
  if (turn.reasoning_tokens > 0) parts.push(`推理 ${formatTokenCount(turn.reasoning_tokens)}`)
  const breakdown = parts.join(' · ')
  return match.mode === 'ordinal'
    ? `${breakdown}\n按会话轮次顺序估算：来源未记录轮次编号`
    : breakdown
}
