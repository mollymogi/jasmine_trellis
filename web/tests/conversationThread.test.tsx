import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { RawEvent } from '../src/api/types'
import { ConversationThread } from '../src/components/ConversationThread'
import { ConversationMessage } from '../src/components/ConversationMessage'
import { agentIdentity } from '../src/lib/agentIdentity'

function message(seq: number, source = 'codex-native-conversation-v1'): RawEvent {
  return { seq, event_id: `evt_${seq}`, event_type: 'assistant.message', actor_kind: 'system', schema_version: 13,
    source_system: source, source_event_id: null, occurred_at: '2026-10-21T15:37:44Z', recorded_at: '2026-10-21T15:37:44Z',
    actor_id: 'act_1', host_id: 'hst_1', session_id: 'ses_A', project_id: 'prj_1', task_id: null,
    payload: { text: `消息 ${seq}` }, body_sha256: 'a'.repeat(64) }
}
const props = { systemEvents: [], taskId: null, loading: false, error: null, loadedAt: null, onReload: vi.fn() }
afterEach(() => { vi.restoreAllMocks() })

it('follows new messages at the bottom but preserves an earlier reading position until asked to return', () => {
  let height = 1800
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('chat-scroll') ? height : 0
  })
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('chat-scroll') ? 400 : 0
  })
  const { rerender } = render(<ConversationThread {...props} messages={[message(1)]} />)
  const viewport = screen.getByRole('region', { name: '对话消息' })
  expect(viewport.scrollTop).toBe(1800)
  height = 2000
  rerender(<ConversationThread {...props} messages={[message(1), message(2)]} />)
  expect(viewport.scrollTop).toBe(2000)
  fireEvent.scroll(viewport, { target: { scrollTop: 800 } })
  height = 2200
  rerender(<ConversationThread {...props} messages={[message(1), message(2), message(3)]} />)
  expect(viewport.scrollTop).toBe(800)
  expect(screen.getByRole('button', { name: /1 条新消息/ })).toBeTruthy()
  rerender(<ConversationThread {...props} messages={[message(1), message(2), message(3)]} loadedAt="2026-10-21T15:40:00Z" />)
  expect(viewport.scrollTop).toBe(800)
  fireEvent.click(screen.getByRole('button', { name: /回到最新/ }))
  expect(viewport.scrollTop).toBe(2200)
  expect(screen.queryByRole('button', { name: /回到最新/ })).toBeNull()
})

it('resets reading state on a different conversation and never scrolls the outer document', () => {
  const scroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(1000)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(300)
  const { rerender } = render(<ConversationThread key="A" {...props} messages={[message(1)]} />)
  fireEvent.scroll(screen.getByRole('region', { name: '对话消息' }), { target: { scrollTop: 100 } })
  rerender(<ConversationThread key="B" {...props} messages={[message(2)]} />)
  expect(screen.getByText(/已定位最新/)).toBeTruthy()
  expect(scroll).not.toHaveBeenCalled()
})

it('labels Codex, Claude, Grok and unknown sources using producer metadata, never message content', () => {
  for (const [source, label] of [['codex-native-conversation-v1', 'Codex'], ['claude-code', 'Claude'], ['grok-build', 'Grok'], ['legacy-import', '未知助手']] as const) {
    const row = message(1, source)
    row.payload.text = 'Codex, Claude and Grok appear in the text; <script>not code</script>'
    const { unmount, container } = render(<ul><ConversationMessage event={row} taskId={null} /></ul>)
    expect(screen.getByRole('img', { name: `${label}头像` })).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('[data-agent-logo]')?.getAttribute('data-agent-logo')).toBe(label === '未知助手' ? 'unknown' : label.toLowerCase())
    unmount()
  }
  const hook = message(2, 'host-hook-v1'); hook.payload.agent = { name: 'claude-code', version: '1' }
  expect(agentIdentity(hook).label).toBe('Claude')
  hook.payload.agent = { name: 'unknown' }
  expect(agentIdentity(hook).kind).toBe('unknown')
})

it('keeps secrets masked in chat bubbles and in copies, even when the original is revealed', async () => {
  const copy = vi.fn(async (_text: string) => {})
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: copy }, configurable: true })
  const row = message(1); row.payload.text = 'api_key=sk-abcdefghijklmnopqrstuvwxyz1234'
  render(<ul><ConversationMessage event={row} taskId={null} /></ul>)
  const text = document.querySelector('.chat-text .rawtext__body')
  expect(text?.textContent).not.toContain('sk-abcdefghijklmnopqrstuvwxyz1234')
  const bubble = within(document.querySelector('.chat-text') as HTMLElement)
  fireEvent.click(bubble.getByRole('button', { name: '显示原文' }))
  expect(text?.textContent).toContain('sk-abcdefghijklmnopqrstuvwxyz1234')
  fireEvent.click(bubble.getByRole('button', { name: '复制脱敏摘要' }))
  await waitFor(() => expect(copy).toHaveBeenCalledTimes(1))
  expect(copy.mock.calls[0]?.[0]).not.toContain('sk-abcdefghijklmnopqrstuvwxyz1234')
})
