import type { PendingConfirmation } from '../api/webConfirmations'
/**
 * In-memory session state (P6-A01).
 *
 * The token is held in a React ref/state pair for the lifetime of the tab and
 * nothing else. There is deliberately no `localStorage`, `sessionStorage`,
 * `IndexedDB`, service worker or cache write anywhere in this bundle: a refresh
 * or "退出" clears the token, and a screenshot of the page shows only bullets.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { CoreClient } from '../api/client'
import { makeEndpoints, type Endpoints } from '../api/endpoints'

/** Read scopes the dashboard asks the operator to grant. The audited writes need
 * more (`interpretations:manage`, `reviews:manage`, `authority:propose|manage`, …);
 * the UI never asks for those, it only reports what Core refuses. */
export const REQUIRED_READ_SCOPES = [
  'objects:read',
  'events:read',
  'state:read',
  'authority:read',
  'evidence:read',
  // P6-02 read slice. The interpretation endpoints also require `events:read`
  // and the review/resolution ones also require `interpretations:read`, which
  // Core enforces server-side; the UI only reports what it is refused.
  'interpretations:read',
  'reviews:read',
  'resolutions:read',
] as const

/** Local auto sign-in (`GET /_local/session`): the dashboard host hands its own page
 * the operator's browser credential on a single-user machine. `checking` until it
 * answers; `unavailable` when the host does not offer it or the token is refused. */
export type AutoSignIn = 'checking' | 'available' | 'unavailable'

export const LOCAL_SIGNIN_TIMEOUT_MS = 5000

/** Ask the dashboard host for the local credential and prove it with an authenticated read. */
export async function fetchLocalToken(fetchImpl: typeof fetch = (...args) => globalThis.fetch(...args)): Promise<string | null> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  // Race the whole operation, including response bodies, so even a transport
  // that ignores cancellation cannot keep the manual sign-in form hidden.
  const deadline = new Promise<null>((resolve) => {
    timer = setTimeout(() => {
      controller.abort()
      resolve(null)
    }, LOCAL_SIGNIN_TIMEOUT_MS)
  })
  try {
    const attempt = async () => {
      const reply = await fetchImpl('/_local/session', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
      if (!reply.ok || controller.signal.aborted) return null
      const token = (await reply.json() as { token?: unknown }).token
      if (typeof token !== 'string' || !token || controller.signal.aborted) return null
      await makeEndpoints(new CoreClient({ getToken: () => token, fetchImpl })).listProjects({ limit: 1, offset: 0 }, controller.signal)
      return controller.signal.aborted ? null : token
    }
    return await Promise.race([attempt(), deadline])
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

interface SessionValue {
  pendingConfirmations: PendingConfirmation[]
  trackConfirmation: (pending: PendingConfirmation) => void
  clearConfirmation: (id: string) => void
  token: string | null
  signedIn: boolean
  signIn: (token: string) => void
  signOut: () => void
  endpoints: Endpoints
  autoSignIn: AutoSignIn
  signInLocally: () => Promise<boolean>
}

const SessionContext = createContext<SessionValue | null>(null)

/** Unit tests mock `fetch` call by call, so the load-time request is opt-in there. */
const LOCAL_SIGNIN_DEFAULT = import.meta.env.MODE !== 'test'

export function SessionProvider({ children, localSignIn = LOCAL_SIGNIN_DEFAULT }: { children: ReactNode; localSignIn?: boolean }): JSX.Element {
  const [pendingConfirmations, setPendingConfirmations] = useState<PendingConfirmation[]>([])
  const trackConfirmation = useCallback((pending: PendingConfirmation) => setPendingConfirmations((rows) => [...rows.filter((row) => row.id !== pending.id), pending]), [])
  const clearConfirmation = useCallback((id: string) => setPendingConfirmations((rows) => rows.filter((row) => row.id !== id)), [])
  const [token, setToken] = useState<string | null>(null)
  // A ref keeps `getToken` stable so the client is not rebuilt on every render,
  // which would restart every in-flight request.
  const tokenRef = useRef<string | null>(null)

  const signIn = useCallback((next: string) => {
    const trimmed = next.trim()
    if (!trimmed) return
    tokenRef.current = trimmed
    setToken(trimmed)
  }, [])

  const signOut = useCallback(() => {
    tokenRef.current = null
    setToken(null)
  }, [])

  const [autoSignIn, setAutoSignIn] = useState<AutoSignIn>(localSignIn ? 'checking' : 'unavailable')
  const signInLocally = useCallback(async () => {
    const local = await fetchLocalToken()
    setAutoSignIn(local ? 'available' : 'unavailable')
    if (local) signIn(local)
    return local !== null
  }, [signIn])
  // Once per page load. After “退出” the page stays signed out; the sign-in page
  // offers the local sign-in again as a button.
  useEffect(() => { if (localSignIn) void signInLocally() }, [localSignIn, signInLocally])

  const endpoints = useMemo(() => {
    const client = new CoreClient({ getToken: () => tokenRef.current })
    return makeEndpoints(client)
  }, [])

  const value = useMemo<SessionValue>(
    () => ({ token, signedIn: token !== null, signIn, signOut, endpoints, pendingConfirmations, trackConfirmation, clearConfirmation, autoSignIn, signInLocally }),
    [token, signIn, signOut, endpoints, pendingConfirmations, trackConfirmation, clearConfirmation, autoSignIn, signInLocally],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (value === null) throw new Error('useSession must be used inside SessionProvider')
  return value
}
