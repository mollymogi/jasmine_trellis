import { useCallback, useEffect, useState } from 'react'

import { DEFAULT_EVENT_LIMIT, type Endpoints, type EventQuery } from '../api/endpoints'
import { CoreApiError, isAbortError } from '../api/errors'
import type { RawEvent } from '../api/types'

export const CONVERSATION_POLL_MS = 3000

type Scope = Pick<EventQuery, 'session_id' | 'task_id' | 'project_id' | 'event_type' | 'source_system'>
interface Snapshot {
  key: string
  items: RawEvent[]
  loading: boolean
  error: CoreApiError | null
  loadedAt: string | null
}
const empty = (key: string): Snapshot => ({ key, items: [], loading: true, error: null, loadedAt: null })

/** Append-only event reads: drain every page, then poll after the last Core seq.
 * Each scope owns its cursor, timer and abort signal. Failed refreshes retain
 * the last successful rows; neither retries nor scope changes can mix sessions.
 */
export function useConversationEvents(endpoints: Endpoints, scope: Scope, enabled = true) {
  const queryKey = JSON.stringify(scope)
  const key = enabled ? queryKey : `disabled:${queryKey}`
  const [snapshot, setSnapshot] = useState<Snapshot>(() => empty(key))
  const [retry, setRetry] = useState(0)
  const reload = useCallback(() => setRetry(value => value + 1), [])

  useEffect(() => {
    if (!enabled) {
      setSnapshot({ ...empty(key), loading: false })
      return
    }
    const query: Scope = JSON.parse(queryKey)
    const controller = new AbortController()
    let running = false
    let cursor: number | null = null
    const rows = new Map<string, RawEvent>()
    setSnapshot(empty(key))

    const read = async () => {
      if (running || controller.signal.aborted) return
      running = true
      try {
        let more = true
        while (more && !controller.signal.aborted) {
          const page = await endpoints.listEvents({ ...query, after_seq: cursor, limit: DEFAULT_EVENT_LIMIT }, controller.signal)
          if (controller.signal.aborted) return
          for (const event of page.events) rows.set(event.event_id, event)
          const last = page.events.reduce((max, event) => Math.max(max, event.seq), cursor ?? 0)
          more = page.count >= DEFAULT_EVENT_LIMIT && last > (cursor ?? 0)
          cursor = last || cursor
          setSnapshot({ key, items: [...rows.values()].sort((a, b) => a.seq - b.seq), loading: more,
            error: null, loadedAt: new Date().toISOString() })
        }
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return
        setSnapshot(previous => ({ ...previous, loading: false,
          error: error instanceof CoreApiError ? error : new CoreApiError({ kind: 'unknown', status: null,
            code: 'ui_unexpected_error', message: error instanceof Error ? error.message : String(error) }) }))
      } finally {
        running = false
      }
    }
    const resume = () => { if (document.visibilityState !== 'hidden') void read() }
    const timer = setInterval(resume, CONVERSATION_POLL_MS)
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('focus', resume)
    void read()
    return () => {
      controller.abort()
      clearInterval(timer)
      document.removeEventListener('visibilitychange', resume)
      window.removeEventListener('focus', resume)
    }
  }, [endpoints, key, queryKey, enabled, retry])

  return { ...(snapshot.key === key ? snapshot : { ...empty(key), loading: enabled }), reload }
}
