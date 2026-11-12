/**
 * Conversation token usage: the session panel above the transcript and the
 * per-turn badges inside it.
 *
 * These tests pin the honesty rules rather than pixel output: a conversation that
 * does not name exactly one native session renders nothing (and asks nothing), a
 * refused read disappears silently, and a message only gets a badge when its turn
 * is genuinely identified — exact `native_turn_id` first, position only when the
 * source records no turn ids at all.
 */

import { act, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { RawEvent } from '../src/api/types'
import { USAGE_SCHEMA, type UsageSessionDetail, type UsageTurn } from '../src/api/usage'
import { ConversationThread } from '../src/components/ConversationThread'
import { ConversationUsage, useConversationUsage } from '../src/components/ConversationUsage'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | undefined
afterEach(() => {
  fake?.restore()
  vi.restoreAllMocks()
})

function event(seq: number, eventType: 'user.prompt' | 'assistant.message', payload: Record<string, unknown>,
  source = 'codex-native-conversation-v1'): RawEvent {
  return {
    seq, event_id: `evt_${seq}`, event_type: eventType,
    actor_kind: eventType === 'user.prompt' ? 'human' : 'system',
    schema_version: 13, source_system: source, source_event_id: null,
    occurred_at: '2026-10-21T15:37:44Z', recorded_at: '2026-10-21T15:37:44Z',
    actor_id: 'act_1', host_id: 'hst_1', session_id: 'ses_A', project_id: 'prj_1', task_id: null,
    payload: { text: `消息 ${seq}`, ...payload }, body_sha256: 'a'.repeat(64),
  }
}

function turn(index: number, total: number, nativeTurnId: string | null = null): UsageTurn {
  return {
    index, native_turn_id: nativeTurnId, step: null, at: null, model: null,
    input_tokens: Math.max(0, total - 200), cached_read_tokens: 0, cache_write_tokens: 0,
    output_tokens: 200, reasoning_tokens: 0, total_tokens: total, cost_usd: null,
  }
}

function sessionDetail(overrides: Partial<UsageSessionDetail> = {}): UsageSessionDetail {
  return {
    schema: USAGE_SCHEMA, agent: 'codex', native_session_id: 'ns_1', project: '/work/app',
    models: ['gpt-5-codex'], started_at: '2026-10-21T15:00:00Z', last_activity_at: '2026-10-21T15:40:00Z',
    totals: {
      input_tokens: 10_000, cached_read_tokens: 60_000, cache_write_tokens: 0, output_tokens: 2345,
      reasoning_tokens: 0, total_tokens: 12_345, cost_usd: 0.0421, sessions: 1, turns: 2, cache_hit_ratio: 0.8,
    },
    turns: [turn(1, 1200), turn(2, 3400)],
    warnings: [],
    ...overrides,
  }
}

const threadProps = { systemEvents: [], taskId: null, loading: false, error: null, loadedAt: null, onReload: vi.fn() }

/** Renders the real hook + panel without the whole dashboard. */
function PanelHarness({ events, token = 'tok_usage' }: { events: RawEvent[]; token?: string | null }) {
  const usage = useConversationUsage(events, token)
  return <ConversationUsage usage={usage} />
}

const flush = async () => {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 0))
  })
}

it('renders session totals, cost and cache hit ratio from the usage endpoint', async () => {
  fake = installFakeCore({ 'GET /_local/usage/session': { body: sessionDetail() } })
  const events = [
    event(1, 'user.prompt', { native_session_id: 'ns_1' }),
    event(2, 'assistant.message', { native_session_id: 'ns_1' }),
  ]
  render(<PanelHarness events={events} />)
  // The loading state keeps the same bordered box, so the transcript below does not jump.
  expect(document.querySelector('.usage-panel--loading')?.getAttribute('aria-busy')).toBe('true')
  await screen.findByText('12.3k')
  const panel = screen.getByRole('region', { name: '本会话 token 合计' })
  expect(within(panel).getByText('本会话 token 合计')).toBeTruthy()
  expect(within(panel).getByText('Codex · 2 轮')).toBeTruthy()
  expect(within(panel).getByText('12.3k')).toBeTruthy()
  expect(within(panel).getByText('10k')).toBeTruthy()
  expect(within(panel).getByText('2.3k')).toBeTruthy()
  expect(within(panel).getByText('60k')).toBeTruthy()
  expect(within(panel).getByText('成本 $0.0421')).toBeTruthy()
  expect(within(panel).getByText('缓存命中率 80%')).toBeTruthy()
  // Cache write and reasoning are reported as zero, so they are shown as zero.
  expect(within(panel).getAllByText('0')).toHaveLength(2)
  const requests = fake.callsTo('GET', '/_local/usage/session')
  expect(requests).toHaveLength(1)
  expect(requests[0]?.query.get('agent')).toBe('codex')
  expect(requests[0]?.query.get('native_session_id')).toBe('ns_1')
  expect(requests[0]?.authorization).toBe('Bearer tok_usage')
})

it('hides a cost the source does not report instead of printing zero', () => {
  render(<ConversationUsage usage={{
    status: 'ready',
    detail: sessionDetail({
      totals: { ...sessionDetail().totals, cost_usd: null, cache_hit_ratio: null },
    }),
  }} />)
  expect(screen.queryByText(/成本/)).toBeNull()
  expect(screen.queryByText(/缓存命中率/)).toBeNull()
})

it('adds badges by ordinal only when both sides record no turn ids', () => {
  const messages = [
    event(1, 'user.prompt', { native_session_id: 'ns_1' }),
    event(2, 'assistant.message', { native_session_id: 'ns_1' }),
    event(3, 'assistant.message', { native_session_id: 'ns_1' }),
  ]
  render(<ConversationThread {...threadProps} messages={messages} usageTurns={sessionDetail().turns} />)
  const first = screen.getByText('≈ 1.2k tokens')
  expect(first.getAttribute('title')).toContain('输入 1k')
  expect(first.getAttribute('title')).toContain('输出 200')
  expect(first.getAttribute('title')).toContain('缓存读 0')
  expect(first.getAttribute('title')).toContain('按会话轮次顺序估算')
  expect(screen.getByText('≈ 3.4k tokens')).toBeTruthy()
  // The user message is never given a turn badge.
  expect(document.querySelectorAll('.chat-message__usage')).toHaveLength(2)
})

it('matches by native turn id before falling back to position', () => {
  const messages = [
    event(1, 'assistant.message', { native_session_id: 'ns_1', native_turn_id: 'turn_b' }),
    event(2, 'assistant.message', { native_session_id: 'ns_1', native_turn_id: 'turn_a' }),
  ]
  render(<ConversationThread {...threadProps} messages={messages}
    usageTurns={[turn(1, 1200, 'turn_a'), turn(2, 3400, 'turn_b')]} />)
  // Position would give the first message 1.2k; its own id says turn_b, so it must say 3.4k.
  expect(screen.getByText('3.4k tokens').getAttribute('title')).toContain('输入 3.2k')
  expect(screen.getByText('1.2k tokens')).toBeTruthy()
  expect(document.querySelectorAll('.chat-message__usage')).toHaveLength(2)
})

it('renders nothing, and asks nothing, when the producer has no usage source', async () => {
  fake = installFakeCore({ 'GET /_local/usage/session': { body: sessionDetail() } })
  const { container } = render(<PanelHarness
    events={[event(1, 'assistant.message', { native_session_id: 'ns_1' }, 'legacy-import')]} />)
  await flush()
  expect(container.firstChild).toBeNull()
  expect(fake.callsTo('GET', '/_local/usage/session')).toHaveLength(0)
})

it('degrades silently when the usage read is refused', async () => {
  fake = installFakeCore({ 'GET /_local/usage/session': { status: 403, body: { error: 'unauthorized' } } })
  const { container } = render(<PanelHarness
    events={[event(1, 'assistant.message', { native_session_id: 'ns_1' })]} />)
  expect(container.querySelector('.usage-panel--loading')).toBeTruthy()
  await flush()
  expect(fake.callsTo('GET', '/_local/usage/session')).toHaveLength(1)
  expect(container.querySelector('.usage-panel')).toBeNull()
  expect(screen.queryByRole('region', { name: '本会话 token 合计' })).toBeNull()
})

it('caches the last detail per native session so returning to it is instant', async () => {
  fake = installFakeCore({}, request => {
    const id = request.query.get('native_session_id') ?? 'ns_1'
    const total = id === 'ns_2' ? 3400 : 1200
    return {
      body: sessionDetail({
        native_session_id: id,
        totals: { ...sessionDetail().totals, input_tokens: total - 200, output_tokens: 200, total_tokens: total },
      }),
    }
  })
  const events = (id: string) => [event(1, 'assistant.message', { native_session_id: id })]
  const { rerender } = render(<PanelHarness events={events('ns_1')} />)
  await screen.findByText('1.2k')
  rerender(<PanelHarness events={events('ns_2')} />)
  await screen.findByText('3.4k')
  rerender(<PanelHarness events={events('ns_1')} />)
  // Rendered from the cache during render: no skeleton and no third request.
  expect(screen.getByText('1.2k')).toBeTruthy()
  await flush()
  expect(fake.callsTo('GET', '/_local/usage/session')).toHaveLength(2)
})
