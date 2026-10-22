import { Bot } from 'lucide-react'
import codex from '../assets/agents/codex.svg'
import claude from '../assets/agents/claude.svg'
import grok from '../assets/agents/grok.svg'
import opencode from '../assets/agents/opencode.svg'
import gemini from '../assets/agents/gemini.svg'
import cursor from '../assets/agents/cursor.svg'
import windsurf from '../assets/agents/windsurf.svg'

const LOGOS: Record<string, string> = { codex, claude, grok, opencode, gemini, cursor, windsurf }
/** Bundled, pinned brand marks. Unknown producers retain a neutral icon. */
export function AgentLogo({ agent, size = 17 }: { agent: string; size?: number }) {
  const source = LOGOS[agent.toLowerCase()]
  return source ? <span className="agent-logo" data-agent-logo={agent.toLowerCase()} aria-hidden="true"
    style={{ width: size, height: size, maskImage: `url("${source}")`, WebkitMaskImage: `url("${source}")` }} />
    : <Bot size={size} data-agent-logo="unknown" aria-hidden="true" />
}
