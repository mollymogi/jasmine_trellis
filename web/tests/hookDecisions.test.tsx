/**
 * Host-hook decision panel (P1 enforcement view) against a fake Core.
 *
 * Pins what the reader sees: the verdict and its matched rule, the filter that narrows the Core
 * request, paging by `before_seq`, and the statement that tool inputs are not read back.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

afterEach(() => {
  fake?.restore()
  fake = null
  window.location.hash = ''
})

const HEALTH = { status: 'ok', core_version: '0.1.0', schema_version: 13 }

function delivery(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    seq: 2,
    delivery_id: 'codex:tool.before:aaaa',
    event: 'tool.before',
    agent: 'codex',
    host_id: 'hst_01M4X85NS8EA42XEFNGP0QAJCN',
    trust: 'NATIVE_HOOK',
    created_at: '2026-10-18T05:00:00Z',
    decision: 'deny',
    guard_decision: 'deny',
    reason: 'Jasmine Authority: no-rm',
    degraded: [],
    matched_rules: [{ rule_id: 'rul_01', rule_key: 'no-rm', version: 1, enforcement: 'DENY', severity: 'HARD' }],
    ...overrides,
  }
}

async function enterRules(
  user: ReturnType<typeof userEvent.setup>,
  deliveries: (query: URLSearchParams) => Record<string, unknown>,
  delayMs: (query: URLSearchParams) => number = () => 0,
): Promise<void> {
  fake = installFakeCore(
    {
      'GET /v1/health': { body: HEALTH },
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /v1/rules':{body:{rules:[],count:0,has_more:false,next_offset:null}},
      'GET /v1/tasks':{body:{tasks:[],count:0}},
      'GET /v1/rules/active': { body: { rules: [], count: 0 } },
    },
    (request) =>
      request.path === '/v1/host-hooks/deliveries'
        ? { body: deliveries(request.query), delayMs: delayMs(request.query) }
        : undefined,
  )
  window.location.hash = '#/rules'
  render(
    <SessionProvider>
      <App />
    </SessionProvider>,
  )
  await user.type(screen.getByLabelText('Core API token'), 'tok')
  await user.click(screen.getByRole('button', { name: '使用此 token' }))
  // Sign-in lands on the overview; go to the rules page explicitly and wait for its decisions panel.
  window.location.hash = '#/rules'
  await user.click(await screen.findByRole('button',{name:'拦截记录'}))
  await screen.findByRole('region', { name: 'Hook 决定记录（Core 已记录）' })
}

describe('Host-hook decisions panel', () => {
  it('distinguishes a local hook refusal from a Core Guard decision', async () => {
    const user = userEvent.setup()
    await enterRules(user, () => ({
      deliveries: [delivery({ decision_source: 'adapter_report', guard_decision: null, matched_rules: [],
                              reason: 'Jasmine Guard cannot resolve this tool call' }),
                   delivery({ seq: 1, delivery_id: 'core:older', decision_source: 'core_guard' })],
      count: 2, has_more: false, next_before_seq: null,
    }))
    const table = await screen.findByTestId('hook-decisions')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows[0]!.textContent).toContain('Hook 上报')
    expect(rows[0]!.textContent).toContain('拒绝')
    expect(rows[0]!.textContent).not.toContain('no-rm')
    expect(rows[1]!.textContent).toContain('Core 计算')
  })

  it('shows the verdict, the matched rule and the reason, and says tool inputs are not read back', async () => {
    const user = userEvent.setup()
    await enterRules(user, () => ({
      deliveries: [
        delivery(),
        delivery({ seq: 1, delivery_id: 'codex:tool.before:bbbb', decision: 'allow', guard_decision: 'allow',
                   reason: null, matched_rules: [] }),
      ],
      count: 2,
      has_more: false,
      next_before_seq: null,
    }))
    const table = await screen.findByTestId('hook-decisions')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]!.textContent).toContain('拒绝')
    expect(rows[0]!.textContent).toContain('no-rm v1 · DENY')
    expect(rows[0]!.textContent).toContain('Jasmine Authority: no-rm')
    expect(rows[1]!.textContent).toContain('放行')
    expect(screen.getByText(/工具输入与会话内容不会被读回/)).toBeTruthy()
  })

  it('narrows the Core request when a decision filter is chosen', async () => {
    const user = userEvent.setup()
    await enterRules(user, () => ({ deliveries: [delivery()], count: 1, has_more: false, next_before_seq: null }))
    await screen.findByTestId('hook-decisions')
    await user.click(screen.getByRole('button', { name: '拒绝' }))
    await waitFor(() => {
      const last = fake!.requests.filter((r) => r.path === '/v1/host-hooks/deliveries').at(-1)
      expect(last?.query.get('decision')).toBe('deny')
    })
  })

  it('pages older decisions with before_seq and appends them', async () => {
    const user = userEvent.setup()
    await enterRules(user, (query) =>
      query.get('before_seq') === '2'
        ? { deliveries: [delivery({ seq: 1, delivery_id: 'codex:tool.before:older', reason: 'older one' })],
            count: 1, has_more: false, next_before_seq: null }
        : { deliveries: [delivery()], count: 1, has_more: true, next_before_seq: 2 })
    const table = await screen.findByTestId('hook-decisions')
    expect(within(table).getAllByRole('row')).toHaveLength(2) // header + one row
    await user.click(screen.getByRole('button', { name: '继续加载 Hook 决定' }))
    await waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(3))
    expect(table.textContent).toContain('older one')
    expect(screen.queryByRole('button', { name: '继续加载 Hook 决定' })).toBeNull()
  })

  it('drops an older page that was still in flight when the filter changed', async () => {
    const user = userEvent.setup()
    await enterRules(
      user,
      (query) => {
        if (query.get('decision') === 'deny') {
          if (query.get('before_seq') === '9') {
            return { deliveries: [delivery({ seq: 8, delivery_id: 'codex:tool.before:deny-older' })],
                     count: 1, has_more: false, next_before_seq: null }
          }
          return { deliveries: [delivery({ seq: 9, delivery_id: 'codex:tool.before:deny', reason: 'deny first' })],
                   count: 1, has_more: true, next_before_seq: 9 }
        }
        return query.get('before_seq') === '2'
          ? { deliveries: [delivery({ seq: 1, delivery_id: 'codex:tool.before:stale', reason: 'stale older' })],
              count: 1, has_more: false, next_before_seq: null }
          : { deliveries: [delivery()], count: 1, has_more: true, next_before_seq: 2 }
      },
      // Hold the unfiltered "older" page in flight while the filter switches to deny.
      (query) => (query.get('before_seq') === '2' ? 150 : 0),
    )
    await screen.findByTestId('hook-decisions')
    await user.click(screen.getByRole('button', { name: '继续加载 Hook 决定' }))
    await waitFor(() => expect(fake!.requests.some((r) => r.query.get('before_seq') === '2')).toBe(true))
    await user.click(screen.getByRole('button', { name: '拒绝' }))
    await waitFor(() => expect(screen.getByTestId('hook-decisions').textContent).toContain('deny first'))
    // Let the stale page's delay run out; it must not land in the deny list.
    await new Promise((resolve) => setTimeout(resolve, 250))
    const table = screen.getByTestId('hook-decisions')
    expect(table.textContent).not.toContain('stale older')
    expect(within(table).getAllByRole('row')).toHaveLength(2) // header + the deny row
    expect(screen.queryByText(/读取更多失败/)).toBeNull()
    // The deny filter keeps its own cursor, so paging continues from before_seq=9.
    await user.click(screen.getByRole('button', { name: '继续加载 Hook 决定' }))
    await waitFor(() => {
      const last = fake!.requests.filter((r) => r.path === '/v1/host-hooks/deliveries').at(-1)
      expect(last?.query.get('decision')).toBe('deny')
      expect(last?.query.get('before_seq')).toBe('9')
    })
  })

  it('states an empty result as empty, not as a refusal', async () => {
    const user = userEvent.setup()
    await enterRules(user, () => ({ deliveries: [], count: 0, has_more: false, next_before_seq: null }))
    expect(await screen.findByText(/Core 还没有记录这一类决定/)).toBeTruthy()
    expect(screen.queryByTestId('hook-decisions')).toBeNull()
  })
})
