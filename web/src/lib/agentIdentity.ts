import type { RawEvent } from '../api/types'

export type AgentKind = 'codex' | 'claude' | 'grok' | 'unknown'

/** Labels describe recorded producer metadata, never words in message text. */
export function agentIdentity(event: RawEvent): { kind: AgentKind; label: string } {
  let producer = event.source_system.toLowerCase()
  if (producer === 'host-hook-v1') {
    const agent = event.payload.agent
    producer = typeof agent === 'object' && agent !== null && 'name' in agent && typeof agent.name === 'string'
      ? agent.name.toLowerCase() : ''
  }
  if (/^codex(?:$|[-_:./])/.test(producer)) return { kind: 'codex', label: 'Codex' }
  if (/^claude(?:$|[-_:./])/.test(producer)) return { kind: 'claude', label: 'Claude' }
  if (/^grok(?:$|[-_:./])/.test(producer)) return { kind: 'grok', label: 'Grok' }
  return { kind: 'unknown', label: '未知助手' }
}
