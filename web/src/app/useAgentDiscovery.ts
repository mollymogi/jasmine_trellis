import { useEffect, useState } from 'react'
import { useSession } from './session'

export type DetectedAgent = { id: string; name: string; cli: boolean; desktop: boolean; detected: boolean; capture_supported: boolean }
export function useAgentDiscovery(enabled = true) {
  const { token } = useSession()
  const [revision, setRevision] = useState(0)
  const [agents, setAgents] = useState<DetectedAgent[]>([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!enabled) return
    const abort = new AbortController()
    setLoading(true); setStatus('')
    void fetch('/_local/agents', { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: abort.signal, cache: 'no-store' })
      .then(async response => { if (!response.ok) throw new Error(); return response.json() })
      .then(data => { if (!Array.isArray(data.agents)) throw new Error(); if (!abort.signal.aborted) setAgents(data.agents) })
      .catch(() => { if (!abort.signal.aborted) { setAgents([]); setStatus('本机 Agent 检测暂不可用，可在 Agent 接入中重试。') } })
      .finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [enabled, revision, token])
  return { agents, status, loading, refresh: () => setRevision(value => value + 1) }
}
export type AgentDiscovery = ReturnType<typeof useAgentDiscovery>
