/**
 * Token usage for the open conversation.
 *
 * Core records what an Agent said and deliberately records no billing metadata, so
 * this panel is a dashboard-local read of the Agent's own session file (see
 * `../api/usage`). Three habits are deliberate:
 *
 * - The panel appears only when the loaded events name exactly one native session
 *   and a known producer. Otherwise it renders nothing: an empty panel or a zero
 *   total would read as "this conversation was free" instead of "unknown".
 * - A failed or refused read also renders nothing. The conversation itself is
 *   still perfectly readable, so usage is an enhancement, not an error state.
 * - The last successful detail is kept per native session, so switching back to a
 *   conversation shows the numbers immediately while a fresh request runs.
 *   The fetch is abortable and a late response for an older session is ignored.
 *   Activity changes and a periodic refresh keep active sessions current.
 */

import { useEffect, useRef, useState } from 'react'
import type { RawEvent } from '../api/types'
import { fetchUsageSession, USAGE_AGENT_LABELS, type UsageSessionDetail } from '../api/usage'
import { deriveUsageTarget, formatCacheHitRatio, formatCostUsd, formatTokenCount } from '../lib/conversationUsage'
import '../styles/usage.css'

export interface ConversationUsageState {
  status: 'idle' | 'loading' | 'ready' | 'error'
  detail: UsageSessionDetail | null
}

interface UsageSnapshot extends ConversationUsageState {
  /** The native session this snapshot describes, so a previous session's numbers
   * are never painted under a newly selected one. */
  key: string | null
}

/** Load one conversation's usage detail. `token` is the local sign-in token; the
 * endpoint authorizes it against Core, and an unsigned tab simply gets no panel. */
export function useConversationUsage(events: RawEvent[], token: string | null): ConversationUsageState {
  const target = deriveUsageTarget(events)
  const key = target ? `${target.agent}:${target.nativeSessionId}` : null
  const cache = useRef(new Map<string, UsageSessionDetail>())
  const [snapshot, setSnapshot] = useState<UsageSnapshot>({ key: null, status: 'idle', detail: null })
  const agent = target?.agent ?? null
  const nativeSessionId = target?.nativeSessionId ?? null

  const activity = events.map(event => event.event_id).join('\u0000')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setRefresh(value => value + 1), 60_000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!agent || !nativeSessionId) {
      setSnapshot(current => current.key === null && current.status === 'idle'
        ? current
        : { key: null, status: 'idle', detail: null })
      return
    }
    const currentKey = `${agent}:${nativeSessionId}`
    const cached = cache.current.get(currentKey)
    const controller = new AbortController()
    let active = true
    setSnapshot({ key: currentKey, status: cached ? 'ready' : 'loading', detail: cached ?? null })
    fetchUsageSession(
      { agent, nativeSessionId },
      { signal: controller.signal, token: token ?? undefined },
    ).then(detail => {
      if (!active || controller.signal.aborted) return
      cache.current.set(currentKey, detail)
      setSnapshot({ key: currentKey, status: 'ready', detail })
    }, () => {
      if (!active || controller.signal.aborted) return
      setSnapshot({ key: currentKey, status: 'error', detail: null })
    })
    return () => {
      active = false
      controller.abort()
    }
  }, [agent, nativeSessionId, token, activity, refresh])

  if (snapshot.key === key) return { status: snapshot.status, detail: snapshot.detail }
  if (key === null) return { status: 'idle', detail: null }
  const cached = cache.current.get(key)
  // A cache hit is known during render, so returning to a conversation does not
  // flash a skeleton for one frame.
  return cached ? { status: 'ready', detail: cached } : { status: 'loading', detail: null }
}

/** One-line panel above the transcript: session totals, turn count and — only when
 * reported — cost and cache hit ratio. */
export function ConversationUsage({ usage }: { usage: ConversationUsageState }): JSX.Element | null {
  if (usage.status === 'idle' || usage.status === 'error' || !usage.detail) {
    if (usage.status !== 'loading') return null
    return <UsageSkeleton />
  }
  const { detail } = usage
  const { totals } = detail
  return <section className="usage-panel" aria-label="本会话 token 合计">
    <header className="usage-panel__head">
      <h3>本会话 token 合计</h3>
      <span className="usage-panel__source">{USAGE_AGENT_LABELS[detail.agent]} · {totals.turns} 轮</span>
    </header>
    <dl className="usage-metrics">
      <UsageMetric label="总" value={totals.total_tokens} />
      <UsageMetric label="输入" value={totals.input_tokens} />
      <UsageMetric label="输出" value={totals.output_tokens} />
      <UsageMetric label="缓存读" value={totals.cached_read_tokens} />
      <UsageMetric label="缓存写" value={totals.cache_write_tokens} />
      <UsageMetric label="推理" value={totals.reasoning_tokens} />
    </dl>
    <p className="usage-panel__meta">
      {totals.cost_usd === null
        ? null
        : <span className="usage-panel__cost">成本 {formatCostUsd(totals.cost_usd)}</span>}
      {totals.cache_hit_ratio === null
        ? null
        : <span>缓存命中率 {formatCacheHitRatio(totals.cache_hit_ratio)}</span>}
      {detail.warnings.length === 0
        ? null
        : <span className="usage-panel__warning" title={detail.warnings.join('\n')}>部分轮次缺少用量明细</span>}
    </p>
  </section>
}

function UsageMetric({ label, value }: { label: string; value: number }): JSX.Element {
  return <div className="usage-metric">
    <dt>{label}</dt>
    <dd>{formatTokenCount(value)}</dd>
  </div>
}

/** Same box as the ready panel so the transcript does not shift when numbers land. */
function UsageSkeleton(): JSX.Element {
  return <section className="usage-panel usage-panel--loading" aria-label="本会话 token 合计" aria-busy="true">
    <header className="usage-panel__head">
      <h3>本会话 token 合计</h3>
      <span className="usage-panel__source">正在读取用量…</span>
    </header>
    <div className="usage-metrics" aria-hidden="true">
      {[0, 1, 2, 3, 4, 5].map(index => <div className="usage-metric" key={index}>
        <span className="usage-skeleton usage-skeleton--label" />
        <span className="usage-skeleton usage-skeleton--value" />
      </div>)}
    </div>
    <p className="usage-panel__meta" aria-hidden="true">
      <span className="usage-skeleton usage-skeleton--meta" />
    </p>
  </section>
}
