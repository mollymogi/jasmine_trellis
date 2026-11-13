/**
 * Token 消耗 view tests.
 *
 * The inventory is a *local* endpoint, so these tests fake `/_local/usage` and
 * `/_local/usage/session` and assert what the page claims, not only what it
 * drew: compact totals, honest cost provenance (only three sources report
 * money), an explanatory empty state instead of "0 tokens", client-side
 * filtering/sorting/paging over the whole inventory, and a drill-down whose
 * loading/error state is independent of the inventory fetch. The router and
 * sidebar wiring is checked too, because a view that cannot be reached is not
 * shipped.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  USAGE_SCHEMA,
  type UsageAgent,
  type UsageAgentRow,
  type UsageDayRow,
  type UsageInventory,
  type UsageModelRow,
  type UsageSessionDetail,
  type UsageSessionRow,
  type UsageSourceStatus,
  type UsageTokens,
  type UsageTotals,
  type UsageTurn,
} from '../src/api/usage'
import { hrefFor, parseHash } from '../src/app/router'
import { SessionProvider, useSession } from '../src/app/session'
import { App } from '../src/App'
import { NAV, isCurrent } from '../src/components/AppSidebar'
import { ConversationUsageView } from '../src/views/ConversationUsageView'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null

beforeEach(() => {
  window.location.hash = ''
})

afterEach(() => {
  fake?.restore()
  fake = null
  vi.restoreAllMocks()
  window.location.hash = ''
})

function tokens(overrides: Partial<UsageTokens> = {}): UsageTokens {
  return {
    input_tokens: 0,
    cached_read_tokens: 0,
    cache_write_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    cost_usd: null,
    ...overrides,
  }
}

function totals(overrides: Partial<UsageTotals> = {}): UsageTotals {
  return { ...tokens(), sessions: 0, turns: 0, cache_hit_ratio: null, ...overrides }
}

function agentRow(agent: UsageAgent, overrides: Partial<UsageTotals> = {}): UsageAgentRow {
  return { ...totals(overrides), agent }
}

function modelRow(
  agent: UsageAgent,
  model: string,
  overrides: Partial<UsageTotals> = {},
): UsageModelRow {
  return { ...totals(overrides), agent, model }
}

function session(overrides: Partial<UsageSessionRow> = {}): UsageSessionRow {
  return {
    agent: 'claude',
    native_session_id: 'ses_default',
    project: 'Default',
    models: ['claude-sonnet-4'],
    started_at: '2026-10-01T00:00:00Z',
    last_activity_at: '2026-10-01T00:10:00Z',
    turns: 1,
    ...tokens({ input_tokens: 4000, output_tokens: 500, total_tokens: 4500 }),
    ...overrides,
  }
}

/** Beta is newest and largest, so the default (total desc) and date order differ at row 2. */
const SESSIONS: UsageSessionRow[] = [
  session({
    agent: 'claude',
    native_session_id: 'ses_beta',
    project: 'Beta',
    models: ['claude-sonnet-4'],
    started_at: '2026-10-03T01:00:00Z',
    last_activity_at: '2026-10-03T01:40:00Z',
    turns: 2,
    ...tokens({
      input_tokens: 4000,
      cached_read_tokens: 2000,
      cache_write_tokens: 60,
      output_tokens: 500,
      reasoning_tokens: 40,
      total_tokens: 6600,
      cost_usd: 0.21,
    }),
  }),
  session({
    agent: 'codex',
    native_session_id: 'ses_alpha',
    project: 'Alpha',
    models: ['gpt-5-codex'],
    started_at: '2026-10-01T03:00:00Z',
    last_activity_at: '2026-10-01T03:30:00Z',
    turns: 2,
    ...tokens({
      input_tokens: 4500,
      cached_read_tokens: 200,
      output_tokens: 300,
      total_tokens: 5000,
      cost_usd: null,
    }),
  }),
  session({
    agent: 'claude',
    native_session_id: 'ses_gamma',
    project: 'Gamma',
    models: ['claude-haiku'],
    started_at: '2026-10-02T02:00:00Z',
    last_activity_at: '2026-10-02T02:05:00Z',
    turns: 1,
    ...tokens({
      input_tokens: 500,
      cached_read_tokens: 300,
      output_tokens: 200,
      total_tokens: 1000,
      cost_usd: 0.01,
    }),
  }),
]

const SOURCES: UsageSourceStatus[] = [
  { agent: 'claude', root: '~/.claude/projects', status: 'ok', sessions: 2 },
  { agent: 'codex', root: '~/.codex/sessions', status: 'ok', sessions: 1 },
  { agent: 'dsh', root: '~/.dsh/sessions', status: 'ok', sessions: 0 },
  { agent: 'grok', root: '~/.grok/sessions', status: 'missing', sessions: 0 },
  {
    agent: 'antigravity',
    root: '~/Library/Application Support/Antigravity',
    status: 'error',
    sessions: 0,
    detail: 'permission denied',
  },
  { agent: 'opencode', root: '~/.local/share/opencode', status: 'ok', sessions: 0 },
]

const BY_AGENT: UsageAgentRow[] = [
  agentRow('claude', {
    input_tokens: 4500,
    cached_read_tokens: 2300,
    cache_write_tokens: 60,
    output_tokens: 700,
    reasoning_tokens: 40,
    total_tokens: 7600,
    cost_usd: 0.22,
    sessions: 2,
    turns: 3,
    cache_hit_ratio: 0.8,
  }),
  agentRow('codex', {
    input_tokens: 4500,
    cached_read_tokens: 200,
    output_tokens: 300,
    total_tokens: 5000,
    sessions: 1,
    turns: 2,
  }),
]

const BY_MODEL: UsageModelRow[] = [
  modelRow('claude', 'claude-sonnet-4', {
    input_tokens: 4000,
    cached_read_tokens: 2000,
    cache_write_tokens: 60,
    output_tokens: 500,
    reasoning_tokens: 40,
    total_tokens: 6600,
    cost_usd: 0.21,
    sessions: 1,
    turns: 2,
    cache_hit_ratio: 0.75,
  }),
  modelRow('claude', 'claude-haiku', {
    input_tokens: 500,
    cached_read_tokens: 300,
    output_tokens: 200,
    total_tokens: 1000,
    cost_usd: 0.01,
    sessions: 1,
    turns: 1,
  }),
  modelRow('codex', 'gpt-5-codex', {
    input_tokens: 4500,
    cached_read_tokens: 200,
    output_tokens: 300,
    total_tokens: 5000,
    sessions: 1,
    turns: 2,
  }),
]

const BY_DAY: UsageDayRow[] = [
  {
    day: '2026-10-03',
    sessions: 1,
    turns: 2,
    ...tokens({
      input_tokens: 4000,
      cached_read_tokens: 2000,
      cache_write_tokens: 60,
      output_tokens: 500,
      reasoning_tokens: 40,
      total_tokens: 6600,
    }),
  },
  {
    day: '2026-10-01',
    sessions: 1,
    turns: 2,
    ...tokens({
      input_tokens: 4500,
      cached_read_tokens: 200,
      output_tokens: 300,
      total_tokens: 5000,
    }),
  },
  {
    day: '2026-10-02',
    sessions: 1,
    turns: 1,
    ...tokens({
      input_tokens: 500,
      cached_read_tokens: 300,
      output_tokens: 200,
      total_tokens: 1000,
    }),
  },
]

function inventory(overrides: Partial<UsageInventory> = {}): UsageInventory {
  return {
    schema: USAGE_SCHEMA,
    generated_at: '2026-10-03T02:00:00Z',
    totals: totals({
      input_tokens: 9000,
      cached_read_tokens: 2500,
      cache_write_tokens: 60,
      output_tokens: 1000,
      reasoning_tokens: 40,
      total_tokens: 12600,
      cost_usd: 0.22,
      sessions: 3,
      turns: 5,
      cache_hit_ratio: 0.75,
    }),
    by_agent: BY_AGENT,
    by_model: BY_MODEL,
    by_day: BY_DAY,
    sessions: SESSIONS,
    sources: SOURCES,
    warnings: ['grok 会话目录不存在，Grok 的消耗未计入'],
    ...overrides,
  }
}

function sessionDetail(overrides: Partial<UsageSessionDetail> = {}): UsageSessionDetail {
  const turns: UsageTurn[] = [
    {
      index: 1,
      native_turn_id: 'turn_1',
      step: 1,
      at: '2026-10-03T01:00:05Z',
      model: 'claude-sonnet-4',
      ...tokens({
        input_tokens: 2000,
        cached_read_tokens: 900,
        output_tokens: 300,
        total_tokens: 3200,
        cost_usd: 0.1,
      }),
    },
    {
      index: 2,
      native_turn_id: 'turn_2',
      step: 2,
      at: '2026-10-03T01:20:05Z',
      model: 'claude-sonnet-4',
      ...tokens({
        input_tokens: 2000,
        cached_read_tokens: 1100,
        cache_write_tokens: 60,
        output_tokens: 200,
        reasoning_tokens: 40,
        total_tokens: 3400,
        cost_usd: 0.11,
      }),
    },
  ]
  return {
    schema: USAGE_SCHEMA,
    agent: 'claude',
    native_session_id: 'ses_beta',
    project: 'Beta',
    models: ['claude-sonnet-4'],
    started_at: '2026-10-03T01:00:00Z',
    last_activity_at: '2026-10-03T01:40:00Z',
    totals: totals({
      input_tokens: 4000,
      cached_read_tokens: 2000,
      cache_write_tokens: 60,
      output_tokens: 500,
      reasoning_tokens: 40,
      total_tokens: 6600,
      cost_usd: 0.21,
      sessions: 1,
      turns: 2,
      cache_hit_ratio: 0.75,
    }),
    turns,
    warnings: [],
    ...overrides,
  }
}

function tileText(key: string): string {
  return screen.getByTestId(`usage-tile-${key}`).textContent ?? ''
}

function projectOrder(): string[] {
  return screen.getAllByTestId('usage-project').map((element) => element.textContent ?? '')
}

/** Signs in without the SignIn form: the view only needs a Bearer token in the session. */
function UsageHarness(): JSX.Element {
  const { signedIn, signIn } = useSession()
  useEffect(() => {
    signIn('tok_usage')
  }, [signIn])
  return signedIn ? <ConversationUsageView /> : <p>正在登录…</p>
}

function renderView(): void {
  render(
    <SessionProvider localSignIn={false}>
      <UsageHarness />
    </SessionProvider>,
  )
}

describe('ConversationUsageView summary', () => {
  it('renders compact totals, cost provenance and sends the Bearer token', async () => {
    fake = installFakeCore({
      'GET /_local/usage': { body: inventory() },
    })
    renderView()

    await screen.findByTestId('usage-tile-total')
    expect(tileText('total')).toBe('12.6k')
    expect(tileText('input')).toBe('9k')
    expect(tileText('output')).toBe('1k')
    expect(tileText('cached-read')).toBe('2.5k')
    expect(tileText('cache-write')).toBe('60')
    expect(tileText('reasoning')).toBe('40')
    expect(tileText('cost')).toBe('$0.22')
    expect(tileText('sessions')).toBe('3')
    expect(tileText('turns')).toBe('5')
    expect(tileText('cache-hit')).toBe('75.0%')

    // Money is labelled as a partial sum, not as the total bill.
    expect(document.body.textContent).toContain('已知成本')
    expect(document.body.textContent).toContain('只有 Claude Code、Grok、OpenCode 报告成本')

    // Both distributions and the day trend are present; the gap is spelled out.
    expect(screen.getByRole('table', { name: '按 Agent 的 token 分布' })).toBeTruthy()
    expect(screen.getByRole('table', { name: '按模型的 token 分布' })).toBeTruthy()
    expect(document.body.textContent).toContain('未找到 ~/.grok/sessions')
    expect(document.body.textContent).toContain('读取失败 · permission denied')
    expect(document.body.textContent).toContain('grok 会话目录不存在，Grok 的消耗未计入')

    const usageCall = fake.callsTo('GET', '/_local/usage')[0]
    expect(usageCall?.authorization).toBe('Bearer tok_usage')
  })

  it('explains missing sources instead of rendering a successful "0 tokens" page', async () => {
    fake = installFakeCore({
      'GET /_local/usage': {
        body: inventory({
          totals: totals(),
          by_agent: [],
          by_model: [],
          by_day: [],
          sessions: [],
          sources: [
            { agent: 'claude', root: '~/.claude/projects', status: 'ok', sessions: 0 },
            { agent: 'codex', root: '~/.codex/sessions', status: 'missing', sessions: 0 },
          ],
          warnings: ['codex 会话目录不存在，Codex 的消耗未计入'],
        }),
      },
    })
    renderView()

    const zero = await screen.findByTestId('usage-zero')
    expect(zero.textContent).toContain('没有读到任何 token 记录')
    expect(zero.textContent).toContain('不等于')
    expect(screen.queryByTestId('usage-tile-total')).toBeNull()
    expect(screen.queryByText(/会话列表/)).toBeNull()
    expect(document.body.textContent).toContain('未找到 ~/.codex/sessions')
    expect(document.body.textContent).toContain('codex 会话目录不存在，Codex 的消耗未计入')
  })
})

describe('ConversationUsageView session list', () => {
  it('filters by agent and searches project/model over the whole inventory', async () => {
    fake = installFakeCore({
      'GET /_local/usage': { body: inventory() },
    })
    const user = userEvent.setup()
    renderView()
    await screen.findAllByTestId('usage-session-row')

    expect(projectOrder()).toEqual(['Beta', 'Alpha', 'Gamma'])

    await user.selectOptions(screen.getByRole('combobox', { name: '会话排序' }), 'started')
    expect(projectOrder()).toEqual(['Beta', 'Gamma', 'Alpha'])

    await user.selectOptions(screen.getByRole('combobox', { name: 'Agent 筛选' }), 'claude')
    expect(projectOrder()).toEqual(['Beta', 'Gamma'])
    expect(screen.getAllByTestId('usage-session-row')).toHaveLength(2)

    await user.type(screen.getByRole('searchbox', { name: '搜索项目或模型' }), 'Gamma')
    expect(projectOrder()).toEqual(['Gamma'])
  })

  it('does not hide a session whose project is unknown', async () => {
    fake = installFakeCore({
      'GET /_local/usage': {
        body: inventory({
          sessions: [
            session({
              agent: 'antigravity',
              native_session_id: 'ses_unknown_project',
              project: null,
              models: [],
              total_tokens: 700,
            }),
          ],
        }),
      },
    })
    renderView()
    await screen.findAllByTestId('usage-session-row')
    expect(projectOrder()).toEqual(['未知项目'])
    expect(document.body.textContent).toContain('未知模型')
  })
})

describe('ConversationUsageView drill-down', () => {
  it('fetches one session on row click and renders its turn rows', async () => {
    fake = installFakeCore({
      'GET /_local/usage': { body: inventory() },
      'GET /_local/usage/session': { body: sessionDetail() },
    })
    const user = userEvent.setup()
    renderView()
    const rows = await screen.findAllByTestId('usage-session-row')
    const firstRow = rows[0]
    if (!firstRow) throw new Error('expected a session row')
    await user.click(firstRow)

    await screen.findByTestId('usage-session-detail')
    await waitFor(() => {
      expect(screen.getAllByTestId('usage-turn-row')).toHaveLength(2)
    })
    expect(document.body.textContent).toContain('第 1 轮')

    const detailCall = fake.callsTo('GET', '/_local/usage/session')[0]
    expect(detailCall?.query.get('agent')).toBe('claude')
    expect(detailCall?.query.get('native_session_id')).toBe('ses_beta')
    expect(detailCall?.authorization).toBe('Bearer tok_usage')
  })

  it('keeps the session list usable when the detail fetch fails', async () => {
    fake = installFakeCore({
      'GET /_local/usage': { body: inventory() },
      'GET /_local/usage/session': { status: 500, body: { error: 'session unreadable' } },
    })
    const user = userEvent.setup()
    renderView()
    const rows = await screen.findAllByTestId('usage-session-row')
    const firstRow = rows[0]
    if (!firstRow) throw new Error('expected a session row')
    await user.click(firstRow)

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('读取该会话的逐轮明细失败')
    expect(alert.textContent).toContain('usage_unavailable')
    expect(screen.getAllByTestId('usage-session-row')).toHaveLength(3)
    expect(screen.queryByTestId('usage-turn-row')).toBeNull()
  })
})

describe('ConversationUsageView failure states', () => {
  it('shows an error panel, not a crash, when the inventory endpoint fails', async () => {
    fake = installFakeCore({
      'GET /_local/usage': { status: 503, body: { error: 'usage index unavailable' } },
    })
    renderView()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('usage_unavailable')
    expect(alert.textContent).toContain('HTTP 503')
    expect(alert.textContent).toContain('usage index unavailable')
    expect(screen.queryByTestId('usage-tile-total')).toBeNull()
  })
})

describe('usage routing and navigation', () => {
  it('parses #/usage, builds its href and marks the sidebar entry current', () => {
    expect(parseHash('#/usage')).toEqual({ name: 'usage' })
    expect(hrefFor({ name: 'usage' })).toBe('#/usage')
    expect(parseHash('#/usage-typo')).toMatchObject({ name: 'unknown' })

    const navItem = NAV.find((item) => item.label === 'Token 消耗')
    expect(navItem?.href).toBe('#/usage')
    expect(isCurrent({ name: 'usage' }, '#/usage')).toBe(true)
    expect(isCurrent({ name: 'usage' }, '#/conversation')).toBe(false)
    expect(isCurrent({ name: 'overview' }, '#/usage')).toBe(false)
  })

  it('renders the view through App at #/usage with the nav entry current', async () => {
    fake = installFakeCore({
      'GET /v1/projects': { body: { projects: [], count: 0 } },
      'GET /_local/usage': { body: inventory() },
    })
    window.location.hash = '#/usage'
    const user = userEvent.setup()
    render(
      <SessionProvider localSignIn={false}>
        <App />
      </SessionProvider>,
    )

    await user.type(screen.getByLabelText('Core API token'), 'tok_usage')
    await user.click(screen.getByRole('button', { name: '使用此 token' }))

    await screen.findByTestId('usage-tile-total')
    const link = screen.getByRole('link', { name: 'Token 消耗' })
    expect(link.getAttribute('aria-current')).toBe('page')
  })
})
