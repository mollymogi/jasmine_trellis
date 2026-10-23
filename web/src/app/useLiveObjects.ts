import { useCallback, useEffect, useRef, useState } from 'react'
import { CoreApiError, isAbortError } from '../api/errors'

const LIMIT = 100
export const OBJECT_POLL_MS = 3000
type Fetch<T> = (offset: number, limit: number, signal: AbortSignal) => Promise<{ items: T[]; count: number }>

/** Core's project/session collections are creation-ordered and have no deletes.
 * Drain all pages, then poll the next offset. Keep good rows on a failed poll;
 * a changed scope aborts the old reader and owns a fresh list and cursor.
 */
export function useLiveObjects<T>(fetch: Fetch<T>, scope: string | null) {
  const fetchRef = useRef(fetch)
  useEffect(() => { fetchRef.current = fetch })
  const [retry, setRetry] = useState(0)
  const [state, setState] = useState<{ scope: string | null; items: T[]; loading: boolean; error: CoreApiError | null }>(
    { scope, items: [], loading: true, error: null },
  )
  const reload = useCallback(() => setRetry(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    const loader = fetchRef.current
    const rows: T[] = []
    let running = false
    setState({ scope, items: [], loading: true, error: null })
    const read = async () => {
      if (running || controller.signal.aborted) return
      running = true
      try {
        let more = true
        while (more && !controller.signal.aborted) {
          const page = await loader(rows.length, LIMIT, controller.signal)
          if (controller.signal.aborted) return
          rows.push(...page.items)
          more = page.count >= LIMIT && page.items.length > 0
          setState({ scope, items: [...rows], loading: more, error: null })
        }
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return
        setState(previous => ({ ...previous, loading: false, error: error instanceof CoreApiError ? error
          : new CoreApiError({ kind: 'unknown', status: null, code: 'ui_unexpected_error', message: String(error) }) }))
      } finally { running = false }
    }
    const resume = () => { if (document.visibilityState !== 'hidden') void read() }
    const timer = setInterval(resume, OBJECT_POLL_MS)
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('focus', resume)
    void read()
    return () => {
      controller.abort()
      clearInterval(timer)
      document.removeEventListener('visibilitychange', resume)
      window.removeEventListener('focus', resume)
    }
  }, [scope, retry])
  return { ...(state.scope === scope ? state : { scope, items: [], loading: true, error: null }), reload }
}
