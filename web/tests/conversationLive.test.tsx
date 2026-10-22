import { act, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CoreClient } from '../src/api/client'
import { makeEndpoints } from '../src/api/endpoints'
import type { RawEvent } from '../src/api/types'
import { SessionProvider } from '../src/app/session'
import { useConversationEvents } from '../src/app/useConversationEvents'
import { ConversationView } from '../src/views/ConversationView'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | undefined
const endpoints = makeEndpoints(new CoreClient({ getToken: () => 'tok_read' }))
function event(seq: number, event_type = 'assistant.message', actor_kind = 'system'): RawEvent {
  return { seq, event_id: `evt_${seq}`, event_type, actor_kind, schema_version: 13,
    source_system: 'codex-native-conversation-v1', source_event_id: null,
    occurred_at: '2026-10-19T15:37:44Z', recorded_at: '2026-10-19T15:37:44Z',
    actor_id: 'act_1', host_id: 'hst_1', session_id: 'ses_A', project_id: 'prj_1', task_id: null,
    payload: { text: `message ${seq}` }, body_sha256: 'a'.repeat(64) }
}
afterEach(() => { fake?.restore(); vi.useRealTimers(); vi.restoreAllMocks() })
const flush = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }

describe('live conversation', () => {
  it('drains more than one page, polls the last seq and retains rows on failure', async () => {
    vi.useFakeTimers()
    let fail = false
    let tail = false
    fake = installFakeCore({}, request => {
      if (fail) return coreError(503, 'unreachable')
      const after = Number(request.query.get('after_seq') ?? 0)
      const rows = after === 0 ? Array.from({ length: 100 }, (_, i) => event(i + 1)) : after === 100 ? [event(101)] : tail && after === 101 ? [event(102)] : []
      return { body: { events: rows, count: rows.length } }
    })
    const { result, unmount } = renderHook(() => useConversationEvents(endpoints, { session_id: 'ses_A' }))
    await flush()
    expect(result.current.items).toHaveLength(101)
    fail = true
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(result.current.error?.status).toBe(503)
    expect(result.current.items).toHaveLength(101)
    fail = false; tail = true
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(result.current.items).toHaveLength(102)
    expect(result.current.error).toBeNull()
    expect(fake.requests.every(r => r.method === 'GET')).toBe(true)
    expect(fake.requests.at(-1)?.query.get('after_seq')).toBe('101')
    const count = fake.requests.length
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(fake.requests).toHaveLength(count)
  })

  it('cancels delayed reads and resets the cursor when switching sessions', async () => {
    vi.useFakeTimers()
    fake = installFakeCore({}, r => ({ delayMs: r.query.get('session_id') === 'ses_A' ? 1000 : 0,
      body: { events: [event(r.query.get('session_id') === 'ses_A' ? 1 : 2)], count: 1 } }))
    const { result, rerender } = renderHook(({ session }) => useConversationEvents(endpoints, { session_id: session }), { initialProps: { session: 'ses_A' } })
    rerender({ session: 'ses_B' })
    await flush()
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(result.current.items.map(e => e.seq)).toEqual([2])
    expect(fake.requests.at(-1)?.query.get('after_seq')).toBeNull()
  })

  it('shows ordered chat with collapsed system records and full message text', async () => {
    const user = event(2, 'user.prompt', 'human')
    const assistant = event(3)
    assistant.payload = { text: '回复正文'.repeat(200) }
    fake = installFakeCore({
      'GET /v1/events': { body: { events: [assistant, event(1, 'session.started'), user], count: 3 } },
      'GET /v1/sessions': { body: { sessions: [{ session_id: 'ses_A', project_id: 'prj_1', task_id: null,
        host_id: 'hst_1', actor_id: 'act_1', status: 'open', revision: 1, source_event_id: 'evt_1',
        started_at: '2026-10-19T15:37:44Z', ended_at: null, created_at: '2026-10-19T15:37:44Z', updated_at: '2026-10-19T15:37:44Z' }], count: 1 } },
      'GET /v1/tasks': { body: { tasks: [], count: 0 } },
      'GET /v1/host-hooks/deliveries': { body: { deliveries: [], count: 0, has_more: false } },
    })
    const { container } = render(<SessionProvider><ConversationView projectId="prj_1" taskId={null} sessionId="ses_A" /></SessionProvider>)
    await screen.findByText('用户消息')
    expect([...container.querySelectorAll('.chat-message')].map(el => el.getAttribute('data-seq'))).toEqual(['2', '3'])
    expect(container.querySelector('.chat-message--assistant .rawtext__body')?.textContent).toBe('回复正文'.repeat(200))
    expect(container.querySelector('.system-events')?.hasAttribute('open')).toBe(false)
    expect(container.querySelector('.chat-message__source')?.hasAttribute('open')).toBe(false)
    expect(container.querySelector('.system-events [data-event-id="evt_1"]')).toBeTruthy()
    expect(screen.getByText(/时间按本地时区/)).toBeTruthy()
  })
})
