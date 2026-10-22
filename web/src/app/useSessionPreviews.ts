import { useEffect, useRef, useState } from 'react'
import type { Endpoints } from '../api/endpoints'
import { agentIdentity } from '../lib/agentIdentity'
import { clip, maskedSummary, payloadText } from '../lib/text'

export interface SessionPreview {
  title: string
  agent: string | null
  state: 'ready' | 'empty' | 'error'
}

/** Immutable first user messages are cached, keyed by Session. Only missing or
 * failed summaries retry. Four readers at most; changing project aborts them.
 */
export function useSessionPreviews(endpoints: Endpoints, sessionIds: string[]) {
  const key = JSON.stringify(sessionIds)
  const cache = useRef(new Map<string, SessionPreview>())
  const [previews, setPreviews] = useState<Record<string, SessionPreview>>({})
  useEffect(() => {
    const ids: string[] = JSON.parse(key)
    const controller = new AbortController()
    let running = false
    const read = async () => {
      if (running || controller.signal.aborted) return
      running = true
      const pending = ids.filter(id => cache.current.get(id)?.state !== 'ready')
      let next = 0
      const worker = async () => {
        while (next < pending.length && !controller.signal.aborted) {
          const id = pending[next++]!
          let result: SessionPreview
          try {
            const page = await endpoints.listEvents({ session_id: id, event_type: 'user.prompt', limit: 1 }, controller.signal)
            if (controller.signal.aborted) return
            const first = page.events.find(event => event.event_type === 'user.prompt' && event.actor_kind === 'human' && event.session_id === id)
            const agent = first ? agentIdentity(first) : null
            result = first ? { title: clip(maskedSummary(payloadText(first.payload) ?? '').replace(/\s+/g, ' ').trim(), 72) || '（空消息）',
              agent: agent?.kind === 'unknown' ? null : agent?.label ?? null, state: 'ready' }
              : { title: '尚无用户消息', agent: null, state: 'empty' }
          } catch {
            if (controller.signal.aborted) return
            result = { title: '会话摘要暂不可用', agent: null, state: 'error' }
          }
          cache.current.set(id, result)
          setPreviews(previous => ({ ...previous, [id]: result }))
        }
      }
      try { await Promise.all(Array.from({ length: Math.min(4, pending.length) }, worker)) }
      finally { running = false }
    }
    const timer = setInterval(() => { if (document.visibilityState !== 'hidden') void read() }, 3000)
    void read()
    return () => { controller.abort(); clearInterval(timer) }
  }, [endpoints, key])
  return previews
}
