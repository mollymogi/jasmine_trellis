// @vitest-environment node
/**
 * Tests for the dashboard-local conversation-usage scanner and its HTTP surface.
 *
 * Every fixture is a synthetic home directory under `os.tmpdir()`: the scanner takes its
 * roots and its command runner from the caller, so a `zstd`-compressed DSH session or a
 * 500 MB OpenCode database is a two-line fake here and the suite never depends on which
 * binaries this machine happens to have.
 */
import http from 'node:http'
import { once } from 'node:events'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  USAGE_NATIVE_SESSION_ID,
  USAGE_SCHEMA,
  createUsageService,
  isUsagePath,
  serveLocalUsage,
} from '../server/conversation-usage.mjs'
import type { UsageCommandResult, UsageCommandRunner } from '../server/conversation-usage.mjs'
import { createDashboardServer } from '../server/serve.mjs'

const homes: string[] = []
const servers: http.Server[] = []

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => { server.closeAllConnections(); server.close(() => resolve()) })))
  await Promise.all(homes.splice(0).map((home) => rm(home, { recursive: true, force: true })))
})

async function makeHome(): Promise<string> {
  const home = await mkdtemp(join(tmpdir(), 'conversation-usage-'))
  homes.push(home)
  return home
}

/** Write one fixture file, creating its directories. Objects become one JSON line. */
async function put(home: string, relative: string, content: string | object): Promise<string> {
  const path = join(home, relative)
  await mkdir(dirname(path), { recursive: true })
  await writeFile(path, typeof content === 'string' ? content : `${JSON.stringify(content)}\n`)
  return path
}

/** Write a JSONL fixture: objects are stringified, strings are written verbatim. */
async function putJsonl(home: string, relative: string, lines: (string | object)[]): Promise<string> {
  return put(home, relative, `${lines.map((line) => (typeof line === 'string' ? line : JSON.stringify(line))).join('\n')}\n`)
}

const ok = (stdout: string): UsageCommandResult => ({ ok: true, code: 0, stdout, stderr: '', truncated: false })
const failed: UsageCommandResult = { ok: false, code: 1, stdout: '', stderr: '', truncated: false }

interface FakeCommands {
  /** Decompressed text for `zstd -dc <path>`; omitted means the frame is unreadable. */
  zstd?: (path: string) => string | undefined
  /** Rows for one `sqlite3 -readonly -json <db> <query>`; `'error'` fails the query. */
  sqlite?: (database: string, query: string) => unknown[] | 'error'
}

/**
 * A command runner that answers `--version` for any candidate (so no binary has to exist)
 * and then serves the canned zstd/sqlite answers. `calls` lets a test assert that the
 * scanner asked for nothing but the documented commands.
 */
function fakeCommands(commands: FakeCommands): { run: UsageCommandRunner; calls: { command: string; args: string[] }[] } {
  const calls: { command: string; args: string[] }[] = []
  const run: UsageCommandRunner = async (command, args) => {
    calls.push({ command, args })
    if (args[0] === '--version') return ok(`${command} 1.0.0`)
    if (args[0] === '-dc') {
      const text = commands.zstd?.(args[1] ?? '')
      return text === undefined ? failed : ok(text)
    }
    if (args[0] === '-readonly') {
      const rows = commands.sqlite?.(args[2] ?? '', args[3] ?? '')
      if (rows === 'error' || rows === undefined) return failed
      return ok(JSON.stringify(rows))
    }
    return failed
  }
  return { run, calls }
}

async function listen(handler: http.RequestListener): Promise<URL> {
  const server = http.createServer(handler)
  servers.push(server)
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return new URL(`http://127.0.0.1:${(server.address() as { port: number }).port}`)
}

/** A stand-in Core: it answers `GET /v1/projects?limit=1` with the current status. */
async function fakeCore(): Promise<{ origin: URL; setStatus: (status: number) => void }> {
  let status = 200
  const origin = await listen((req, res) => {
    // Core is the authority on the token: a bad Bearer fails here, not in the dashboard.
    const accepted = req.headers.authorization === 'Bearer valid'
    res.writeHead(accepted ? status : 401)
    res.end('{"projects":[]}')
  })
  return { origin, setStatus: (next: number) => { status = next } }
}

const SESSION = '11111111-2222-3333-4444-555555555555'

/** One Claude Code project with a parent file, a subagent file and a real cost snapshot. */
async function claudeHome(): Promise<string> {
  const home = await makeHome()
  const project = '.claude/projects/-work-app'
  await putJsonl(home, `${project}/${SESSION}.jsonl`, [
    { type: 'user', uuid: 'u1', parentUuid: null, promptId: 'prompt-1', sessionId: SESSION, cwd: '/work/app', timestamp: '2026-01-01T00:00:00.000Z' },
    {
      type: 'assistant',
      uuid: 'a1',
      parentUuid: 'u1',
      sessionId: SESSION,
      cwd: '/work/app',
      timestamp: '2026-01-01T00:00:05.000Z',
      message: {
        id: 'msg_1',
        model: 'claude-test-1',
        usage: { input_tokens: 10, cache_creation_input_tokens: 2, cache_read_input_tokens: 100, output_tokens: 5, output_tokens_details: { thinking_tokens: 3 } },
      },
    },
    // The same assistant message again with a new uuid: byte-identical usage, and summing
    // lines instead of ids would double the session.
    {
      type: 'assistant',
      uuid: 'a2',
      parentUuid: 'u1',
      sessionId: SESSION,
      cwd: '/work/app',
      timestamp: '2026-01-01T00:00:06.000Z',
      message: { id: 'msg_1', model: 'claude-test-1', usage: { input_tokens: 10, cache_creation_input_tokens: 2, cache_read_input_tokens: 100, output_tokens: 5, output_tokens_details: { thinking_tokens: 3 } } },
    },
    { type: 'api-request', uuid: 'r1', parentUuid: 'a2', sessionId: SESSION, cwd: '/work/app' },
    { type: 'cost-state', totalCostUSD: 1.5, sessionId: SESSION, costStateVersion: 2 },
  ])
  await putJsonl(home, `${project}/${SESSION}/subagents/agent-1.jsonl`, [
    { type: 'user', uuid: 'su1', parentUuid: null, promptId: 'prompt-sub', sessionId: SESSION, isSidechain: true, cwd: '/work/app', timestamp: '2026-01-01T00:01:00.000Z' },
    {
      type: 'assistant',
      uuid: 'sa1',
      parentUuid: 'su1',
      sessionId: SESSION,
      isSidechain: true,
      cwd: '/work/app',
      timestamp: '2026-01-01T00:01:05.000Z',
      message: {
        id: 'msg_2',
        model: 'claude-test-1',
        usage: { input_tokens: 1, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, output_tokens: 0, output_tokens_details: { thinking_tokens: 0 } },
      },
    },
  ])
  return home
}

describe('claude', () => {
  it('sums a session with its subagent file and deduplicates repeated messages', async () => {
    const home = await claudeHome()
    const inventory = await createUsageService({ home }).readUsageInventory()
    expect(inventory.schema).toBe(USAGE_SCHEMA)

    const session = inventory.sessions.find((row) => row.agent === 'claude')
    expect(session).toMatchObject({
      native_session_id: SESSION,
      // The line's own cwd is authoritative; the encoded directory name is never decoded.
      project: '/work/app',
      models: ['claude-test-1'],
      turns: 2,
      input_tokens: 11,
      cached_read_tokens: 100,
      cache_write_tokens: 2,
      output_tokens: 5,
      reasoning_tokens: 3,
      // input+cacheRead+cacheWrite+output, the contract's total for a Claude session.
      total_tokens: 118,
      cost_usd: 1.5,
    })
    expect(session?.started_at).toBe('2026-01-01T00:00:00.000Z')
    expect(session?.last_activity_at).toBe('2026-01-01T00:01:05.000Z')

    const detail = await createUsageService({ home }).readUsageSession('claude', SESSION)
    expect(detail?.totals).toMatchObject({ sessions: 1, turns: 2, total_tokens: 118, cost_usd: 1.5 })
    expect(detail?.turns.map((turn) => turn.index)).toEqual([1, 2])
    // A turn id comes from the owning user line, found by walking parentUuid.
    expect(detail?.turns.map((turn) => turn.native_turn_id)).toEqual(['prompt-1', 'prompt-sub'])
    expect(detail?.turns[0]).toMatchObject({ model: 'claude-test-1', step: null, at: '2026-01-01T00:00:05.000Z' })
  })

  it('skips a lock file, a corrupt line and a half-written trailing record', async () => {
    const home = await makeHome()
    const project = '.claude/projects/-work-app'
    await putJsonl(home, `${project}/${SESSION}.jsonl`, [
      { type: 'user', uuid: 'u1', parentUuid: null, promptId: 'prompt-1', sessionId: SESSION, cwd: '/work/app', timestamp: '2026-01-01T00:00:00.000Z' },
      // Not JSON at all.
      '{"type":"assistant","uuid":"broken"',
      {
        type: 'assistant',
        uuid: 'a1',
        parentUuid: 'u1',
        sessionId: SESSION,
        cwd: '/work/app',
        timestamp: '2026-01-01T00:00:05.000Z',
        message: { id: 'msg_1', model: 'claude-test-1', usage: { input_tokens: 7, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, output_tokens: 3 } },
      },
    ])
    // The scanner must never treat a lock file as a session.
    await put(home, `${project}/${SESSION}.jsonl.lock`, '')
    // A trailing record with no newline is the normal state of a session being written.
    await writeFile(join(home, project, `${SESSION}.jsonl`), '{"type":"assistant","uuid":"a2","parentUuid":"u1","message":{"id":"msg_', { flag: 'a' })

    const service = createUsageService({ home })
    const inventory = await service.readUsageInventory()
    const session = inventory.sessions.find((row) => row.agent === 'claude')
    expect(session?.native_session_id).toBe(SESSION)
    expect(session?.turns).toBe(1)
    expect(session?.total_tokens).toBe(10)
    // The damage is reported, not thrown.
    expect(inventory.warnings.join('\n')).toMatch(/claude/)
  })
})

describe('codex', () => {
  const codexPath = () => `.codex/sessions/2026/02/01/rollout-2026-02-01T10-00-00-${SESSION}.jsonl`

  it('uses the thread cumulative and the turn model, never the sum of cumulative turns', async () => {
    const home = await makeHome()
    const usage = (total: number) => ({ input_tokens: total - 10, cached_input_tokens: 0, cache_write_input_tokens: 0, output_tokens: 10, reasoning_output_tokens: 3, total_tokens: total })
    await putJsonl(home, codexPath(), [
      { type: 'session_meta', payload: { id: SESSION, session_id: SESSION, cwd: '/work/codex', timestamp: '2026-02-01T10:00:00.000Z', model_provider: 'openai' } },
      { type: 'turn_context', payload: { turn_id: 't1', model: 'gpt-test-1' } },
      // Per response: 100, 200, 300. Turn cumulatives climb 100 -> 300 -> 600, thread
      // cumulatives do too. Summing the cumulative fields would report 1000 or 1200.
      { type: 'token_usage_record', payload: { turn_id: 't1', usage: usage(100), turn_token_usage: usage(100), thread_token_usage: usage(100) } },
      { type: 'token_usage_record', payload: { turn_id: 't1', usage: usage(200), turn_token_usage: usage(300), thread_token_usage: usage(300) } },
      { type: 'token_usage_record', payload: { turn_id: 't1', usage: usage(300), turn_token_usage: usage(600), thread_token_usage: usage(600) } },
    ])

    const service = createUsageService({ home })
    const inventory = await service.readUsageInventory()
    const session = inventory.sessions.find((row) => row.agent === 'codex')
    expect(session).toMatchObject({
      native_session_id: SESSION,
      project: '/work/codex',
      models: ['gpt-test-1'],
      turns: 3,
      // Per-response inputs are 90/190/290; the totals add up to the thread cumulative.
      input_tokens: 570,
      output_tokens: 30,
      reasoning_tokens: 9,
      total_tokens: 600,
      cost_usd: null,
    })

    const detail = await service.readUsageSession('codex', SESSION)
    expect(detail?.turns).toHaveLength(3)
    expect(detail?.turns.map((turn) => turn.native_turn_id)).toEqual(['t1', 't1', 't1'])
    expect(detail?.turns[2]?.total_tokens).toBe(300)
    expect(detail?.totals.total_tokens).toBe(600)
  })

  it('reads a large rollout from its tail without parsing the whole file', async () => {
    const home = await makeHome()
    const path = join(home, codexPath())
    await mkdir(dirname(path), { recursive: true })
    const head = [
      JSON.stringify({ type: 'session_meta', payload: { id: SESSION, session_id: SESSION, cwd: '/work/big', timestamp: '2026-02-01T10:00:00.000Z' } }),
      JSON.stringify({ type: 'turn_context', payload: { turn_id: 't1', model: 'gpt-test-1' } }),
      JSON.stringify({ type: 'token_usage_record', payload: { turn_id: 't1', usage: { input_tokens: 90, output_tokens: 10 } } }),
    ]
    const filler = `${Array.from({ length: 40_000 }, (_, index) => JSON.stringify({ type: 'response_item', payload: { index, text: 'x'.repeat(180) } })).join('\n')}\n`
    const tail = [
      { type: 'turn_context', payload: { turn_id: 't2', model: 'gpt-test-2' } },
      { type: 'token_usage_record', payload: { turn_id: 't1', usage: { input_tokens: 900, output_tokens: 100, total_tokens: 1000 }, thread_token_usage: { input_tokens: 900, output_tokens: 100, total_tokens: 1000 } } },
      { type: 'token_usage_record', payload: { turn_id: 't1', usage: { input_tokens: 1800, output_tokens: 200, total_tokens: 2000 }, thread_token_usage: { input_tokens: 1900, output_tokens: 100, total_tokens: 2000 } } },
    ]
    await writeFile(path, `${head.join('\n')}\n${filler}${tail.map((line) => JSON.stringify(line)).join('\n')}\n`)

    const inventory = await createUsageService({ home }).readUsageInventory()
    const session = inventory.sessions.find((row) => row.agent === 'codex')
    expect(session?.native_session_id).toBe(SESSION)
    expect(session?.project).toBe('/work/big')
    expect(session?.total_tokens).toBe(2000)
    expect(session?.turns).toBe(3)
    expect(inventory.by_model.find(row => row.model === 'gpt-test-1')?.total_tokens).toBe(100)
    expect(inventory.by_model.find(row => row.model === 'unknown')?.total_tokens).toBe(1900)
    expect(inventory.by_model.reduce((sum, row) => sum + row.total_tokens, 0)).toBe(2000)
  })
})

describe('dsh', () => {
  const dshPath = `.dsh/sessions/--work-dsh--/session-${SESSION}/session.v4.jsonl.zstd`

  it('decompresses a v4 session and defaults the fields v4 omits to zero', async () => {
    const home = await makeHome()
    await put(home, dshPath, 'compressed-placeholder')
    const compressed = [
      JSON.stringify({ type: 'session', version: 4, id: `session-${SESSION}`, createdAt: 1767225600000, cwd: '/work/dsh' }),
      JSON.stringify({
        type: 'assistant/message',
        time: 1767225601000,
        data: { turn: 1, step: 2, message: { id: 'msg-1', source: { kind: 'model', provider: 'magpie', model: 'vendor/dsh-model' } }, usage: { inputTokens: 10, outputTokens: 5, totalTokens: 15 } },
      }),
      JSON.stringify({
        type: 'assistant/message',
        time: 1767225602000,
        data: { turn: 2, step: 1, message: { id: 'msg-2', source: { kind: 'model', model: 'vendor/dsh-model' } }, usage: { inputTokens: 1, outputTokens: 2, totalTokens: 7, cacheReadTokens: 4, reasoningTokens: 6 } },
      }),
      // Usage lives only on `assistant/message`; a step end carries none.
      JSON.stringify({ type: 'step/end', time: 1767225603000, data: { step: 2 } }),
    ].join('\n')
    const { run, calls } = fakeCommands({ zstd: () => compressed })

    const service = createUsageService({ home, run })
    const inventory = await service.readUsageInventory()
    const session = inventory.sessions.find((row) => row.agent === 'dsh')
    expect(session).toMatchObject({
      native_session_id: `session-${SESSION}`,
      project: '/work/dsh',
      models: ['vendor/dsh-model'],
      turns: 2,
      input_tokens: 11,
      cached_read_tokens: 4,
      output_tokens: 7,
      reasoning_tokens: 6,
      total_tokens: 22,
      cost_usd: null,
    })
    const detail = await service.readUsageSession('dsh', `session-${SESSION}`)
    expect(detail?.turns[0]).toMatchObject({ native_turn_id: 'msg-1', step: 2, model: 'vendor/dsh-model' })
    expect(calls.some((call) => call.args[0] === '-dc')).toBe(true)
    // Only the documented invocation: no shell, no second decompressor flag set.
    expect(calls.filter((call) => call.args[0] === '-dc').every((call) => call.args.length === 2)).toBe(true)
  })

  it('reports the source as an error when zstd is unavailable', async () => {
    const home = await makeHome()
    await put(home, dshPath, 'compressed-placeholder')
    const run: UsageCommandRunner = async (_command, args) => (args[0] === '--version' ? failed : failed)
    const inventory = await createUsageService({ home, run }).readUsageInventory()
    const source = inventory.sources.find((row) => row.agent === 'dsh')
    expect(source?.status).toBe('error')
    expect(source?.detail).toMatch(/zstd/)
    expect(inventory.sessions.filter((row) => row.agent === 'dsh')).toHaveLength(0)
  })
})

describe('grok', () => {
  it('converts costUsdTicks to USD and never invents a turn id', async () => {
    const home = await makeHome()
    await put(home, `.grok/sessions/%2Fwork%2Fgrok/${SESSION}/usage.json`, {
      sessionId: SESSION,
      updatedAt: 1767225600000,
      session: {
        inputTokens: 74_000,
        cachedReadTokens: 60_000,
        cacheCreationTokens: 500,
        outputTokens: 3_500,
        reasoningTokens: 1_200,
        totalTokens: 77_500,
        modelCalls: 2,
        costUsdTicks: 451_989_200,
        turnCount: 2,
        primaryModelId: 'grok-test-1',
      },
      turns: [
        { turnNumber: 1, endedAt: '2026-01-01T00:00:10.000Z', inputTokens: 40_000, cachedReadTokens: 30_000, cacheCreationTokens: 500, outputTokens: 2_000, reasoningTokens: 700, totalTokens: 42_000, costUsdTicks: 250_000_000, primaryModelId: 'grok-test-1' },
        { turnNumber: 2, endedAt: '2026-01-01T00:00:20.000Z', inputTokens: 34_000, cachedReadTokens: 30_000, cacheCreationTokens: 0, outputTokens: 1_500, reasoningTokens: 500, totalTokens: 35_500, costUsdTicks: 201_989_200, primaryModelId: 'grok-test-1' },
      ],
    })

    const service = createUsageService({ home })
    const inventory = await service.readUsageInventory()
    const session = inventory.sessions.find((row) => row.agent === 'grok')
    expect(session).toMatchObject({
      native_session_id: SESSION,
      // The directory name is the percent-encoded cwd and decodes losslessly.
      project: '/work/grok',
      models: ['grok-test-1'],
      turns: 2,
      // Grok's inputTokens includes the cached read, so it is subtracted once.
      input_tokens: 14_000,
      cached_read_tokens: 60_000,
      cache_write_tokens: 500,
      output_tokens: 3_500,
      reasoning_tokens: 1_200,
      // The five components sum to 78,000; Grok's own totalTokens (77,500) omits the
      // cache-creation tokens, so the contract total is the component sum for every agent.
      total_tokens: 78_000,
      // 451,989,200 nano-dollar ticks = $0.4519892.
      cost_usd: 0.4519892,
    })

    const detail = await service.readUsageSession('grok', SESSION)
    expect(detail?.turns.map((turn) => turn.index)).toEqual([1, 2])
    expect(detail?.turns.map((turn) => turn.native_turn_id)).toEqual([null, null])
    expect(detail?.turns[1]?.at).toBe('2026-01-01T00:00:20.000Z')
    expect(detail?.totals.cost_usd).toBe(0.4519892)
  })
})

describe('antigravity', () => {
  it('totals input + cache read + output and keeps the working directory from the summaries index', async () => {
    const home = await makeHome()
    const transcript = `.gemini/antigravity/brain/${SESSION}/.system_generated/logs/transcript_full.jsonl`
    await putJsonl(home, transcript, [
      { step_index: 1, source: 'MODEL', type: 'PLANNER_RESPONSE', status: 'DONE', created_at: '2026-10-03T14:54:18Z', input_tokens: 100, cache_read_tokens: 900, output_tokens: 10 },
      { step_index: 2, source: 'MODEL', type: 'PLANNER_RESPONSE', status: 'DONE', created_at: '2026-10-03T14:54:20Z', input_tokens: 0, cache_read_tokens: 0, output_tokens: 5 },
      // A checkpoint line carries no counters and must not become a turn.
      { step_index: 3, type: 'CHECKPOINT', status: 'DONE', created_at: '2026-10-03T14:54:21Z' },
    ])
    await put(home, '.gemini/antigravity/conversation_summaries.db', 'sqlite-placeholder')
    const { run } = fakeCommands({
      sqlite: (database, query) => {
        if (!database.endsWith('conversation_summaries.db')) return 'error'
        if (!query.includes('conversation_summaries')) return 'error'
        return [{ conversation_id: SESSION, workspace_uris: '["file:///work/antigravity"]' }]
      },
    })

    const service = createUsageService({ home, run })
    const inventory = await service.readUsageInventory()
    const session = inventory.sessions.find((row) => row.agent === 'antigravity')
    expect(session).toMatchObject({
      native_session_id: SESSION,
      project: '/work/antigravity',
      // The transcript names no model; `unknown` is the honest value.
      models: ['unknown'],
      turns: 2,
      input_tokens: 100,
      cached_read_tokens: 900,
      output_tokens: 15,
      cache_write_tokens: 0,
      // Disjoint counters: input excludes the cache read.
      total_tokens: 1015,
      cost_usd: null,
    })

    const detail = await service.readUsageSession('antigravity', SESSION)
    expect(detail?.turns.map((turn) => turn.step)).toEqual([1, 2])
    // Core names an Antigravity turn `step-<step_index>`; that is the badge key.
    expect(detail?.turns.map((turn) => turn.native_turn_id)).toEqual(['step-1', 'step-2'])
    expect(detail?.turns[0]).toMatchObject({ model: null })
  })
})

describe('opencode', () => {
  it('keeps cache-bearing detail consistent and re-queries unchanged database files after TTL', async () => {
    const home = await makeHome()
    await put(home, '.local/share/opencode/opencode.db', 'sqlite-placeholder')
    let now = 0
    let input = 1000
    const { run } = fakeCommands({ sqlite: (_database, query) => {
      if (query.includes('select id, data')) return [{ id: 'msg_1', data: JSON.stringify({
        modelID: 'm1', tokens: { input, output: 100, reasoning: 50, cache: { read: 800, write: 20 } },
      }) }]
      if (query.includes('from message')) return [{ session_id: 'ses_1', turns: 1 }]
      return [{ id: 'ses_1', model: 'm1', tokens_input: input, tokens_output: 100,
        tokens_reasoning: 50, tokens_cache_read: 800, tokens_cache_write: 20 }]
    } })
    const service = createUsageService({ home, run, now: () => now, limits: { cacheTtlMs: 10 } })
    const detail = await service.readUsageSession('opencode', 'ses_1')
    expect(detail?.totals.total_tokens).toBe(1970)
    expect(detail?.turns[0]?.input_tokens).toBe(1000)
    input = 2000
    now = 11
    expect((await service.readUsageInventory()).totals.total_tokens).toBe(2970)
  })

  it('reads its rollup through sqlite3 and reports USD', async () => {
    const home = await makeHome()
    await put(home, '.local/share/opencode/opencode.db', 'sqlite-placeholder')
    const { run, calls } = fakeCommands({
      sqlite: (_database, query) => {
        if (query.includes('from message')) return [{ session_id: 'ses_1', turns: 3 }]
        if (query.includes('from session')) {
          return [
            {
              id: 'ses_1',
              directory: '/work/opencode',
              model: '{"id":"m1","providerID":"prov"}',
              cost: 4.5,
              tokens_input: 1000,
              tokens_output: 100,
              tokens_reasoning: 50,
              tokens_cache_read: 800,
              tokens_cache_write: 20,
              time_created: 1767225600000,
              time_updated: 1767225700000,
            },
          ]
        }
        return 'error'
      },
    })

    const inventory = await createUsageService({ home, run }).readUsageInventory()
    const session = inventory.sessions.find((row) => row.agent === 'opencode')
    expect(session).toMatchObject({
      native_session_id: 'ses_1',
      project: '/work/opencode',
      models: ['prov/m1'],
      turns: 3,
      // The session columns are already disjoint, so nothing is subtracted; reasoning is
      // folded into output so that every agent's output_tokens means the same thing.
      input_tokens: 1000,
      cached_read_tokens: 800,
      cache_write_tokens: 20,
      output_tokens: 150,
      reasoning_tokens: 50,
      total_tokens: 1970,
      cost_usd: 4.5,
    })
    expect(inventory.totals.cost_usd).toBe(4.5)
    // Two bounded reads, both read-only, both against the fixed database path.
    const reads = calls.filter((call) => call.args[0] === '-readonly')
    expect(reads).toHaveLength(2)
    expect(reads.every((call) => call.args[1] === '-json' && call.args[2] === join(home, '.local/share/opencode/opencode.db'))).toBe(true)
  })

  it('reports the source as an error when sqlite3 is unavailable', async () => {
    const home = await makeHome()
    await put(home, '.local/share/opencode/opencode.db', 'sqlite-placeholder')
    const run: UsageCommandRunner = async () => failed
    const inventory = await createUsageService({ home, run }).readUsageInventory()
    const source = inventory.sources.find((row) => row.agent === 'opencode')
    expect(source?.status).toBe('error')
    expect(source?.detail).toMatch(/sqlite3/)
  })
})

describe('inventory arithmetic', () => {
  it('reports every source as missing on a home with no agent data', async () => {
    const home = await makeHome()
    const inventory = await createUsageService({ home }).readUsageInventory()
    expect(inventory.sources).toHaveLength(6)
    expect(inventory.sources.every((source) => source.status === 'missing')).toBe(true)
    expect(inventory.sessions).toEqual([])
    expect(inventory.totals).toMatchObject({ sessions: 0, turns: 0, total_tokens: 0, cost_usd: null, cache_hit_ratio: null })
    // A source detail explains the absence without naming the operator's home directory.
    expect(inventory.sources.find((source) => source.agent === 'claude')?.root).toBe('~/.claude/projects')
    expect(JSON.stringify(inventory)).not.toContain(home)
  })

  it('keeps totals, by_agent, by_model, by_day and the session rows in agreement', async () => {
    const home = await claudeHome()
    await put(home, `.grok/sessions/%2Fwork%2Fgrok/${SESSION}/usage.json`, {
      updatedAt: 1767225600000,
      session: {
        inputTokens: 30_000,
        cachedReadTokens: 20_000,
        cacheCreationTokens: 0,
        outputTokens: 1_000,
        reasoningTokens: 0,
        totalTokens: 31_000,
        costUsdTicks: 1_000_000_000,
        turnCount: 0,
        primaryModelId: 'grok-test-1',
      },
      turns: [],
    })
    const inventory = await createUsageService({ home }).readUsageInventory()

    const sum = (rows: { total_tokens: number }[]) => rows.reduce((total, row) => total + row.total_tokens, 0)
    expect(sum(inventory.sessions)).toBe(inventory.totals.total_tokens)
    expect(sum(inventory.by_agent)).toBe(inventory.totals.total_tokens)
    // A session with no readable timestamp is left out of by_day, with a warning.
    expect(sum(inventory.by_day)).toBe(inventory.totals.total_tokens)
    expect(inventory.totals.sessions).toBe(inventory.sessions.length)
    expect(inventory.by_agent.reduce((total, row) => total + row.sessions, 0)).toBe(inventory.totals.sessions)
    expect(inventory.by_day.every((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.day))).toBe(true)

    const claudeRow = inventory.by_agent.find((row) => row.agent === 'claude')
    // cached_read / (input + cached_read + cache_write) = 100 / 113
    expect(claudeRow?.cache_hit_ratio).toBeCloseTo(100 / 113, 10)
    expect(inventory.totals.cost_usd).toBeCloseTo(2.5, 10)
    expect(inventory.by_model.every((row) => row.sessions > 0)).toBe(true)
  })

  it('serves a cached inventory inside the TTL and re-reads after invalidate', async () => {
    const home = await claudeHome()
    const service = createUsageService({ home, limits: { cacheTtlMs: 60_000 } })
    const first = await service.readUsageInventory()
    expect(await service.readUsageInventory()).toBe(first)
    service.invalidate()
    expect(await service.readUsageInventory()).not.toBe(first)
  })
})

describe('http surface', () => {
  it('refuses a non-loopback host, a cross-site request and a foreign origin before scanning', async () => {
    const home = await claudeHome()
    const service = createUsageService({ home })
    const core = await fakeCore()
    const host = await listen((req, res) => { void serveLocalUsage(req, res, core.origin, service) })

    const hostHeader = new Promise<number>((resolve) => {
      http.get(host, { headers: { Authorization: 'Bearer valid', Host: 'evil.invalid' } }, (response) => { response.resume(); resolve(response.statusCode ?? 0) })
    })
    expect(await hostHeader).toBe(403)
    expect((await fetch(host, { headers: { Authorization: 'Bearer valid', 'sec-fetch-site': 'cross-site' } })).status).toBe(403)
    expect((await fetch(host, { headers: { Authorization: 'Bearer valid', Origin: 'http://evil.invalid' } })).status).toBe(403)
  })

  it('requires a Bearer token that Core accepts', async () => {
    const home = await claudeHome()
    const service = createUsageService({ home })
    const core = await fakeCore()
    const host = await listen((req, res) => { void serveLocalUsage(req, res, core.origin, service) })

    const inventoryPath = new URL('/_local/usage', host)
    expect((await fetch(inventoryPath)).status).toBe(401)
    const refused = await fetch(inventoryPath, { headers: { Authorization: 'Bearer bad' } })
    expect(refused.status).toBe(401)
    expect(await refused.json()).toEqual({ error: 'core_authorization_failed' })
    // Core's own refusal is passed through unchanged; any other failure is a 502.
    core.setStatus(403)
    expect((await fetch(inventoryPath, { headers: { Authorization: 'Bearer valid' } })).status).toBe(403)
    core.setStatus(500)
    expect((await fetch(inventoryPath, { headers: { Authorization: 'Bearer valid' } })).status).toBe(502)
  })

  it('answers the inventory and one session detail with the contract shapes', async () => {
    const home = await claudeHome()
    const service = createUsageService({ home })
    const core = await fakeCore()
    const host = await listen((req, res) => { void serveLocalUsage(req, res, core.origin, service) })
    const headers = { Authorization: 'Bearer valid' }

    const inventory = await fetch(new URL('/_local/usage', host), { headers })
    expect(inventory.status).toBe(200)
    expect(inventory.headers.get('cache-control')).toBe('no-store')
    expect(inventory.headers.get('x-content-type-options')).toBe('nosniff')
    const body = (await inventory.json()) as { schema: string; by_agent: { agent: string }[]; sessions: { native_session_id: string }[] }
    expect(body.schema).toBe(USAGE_SCHEMA)
    expect(body.by_agent.map((row) => row.agent)).toEqual(['claude', 'codex', 'dsh', 'grok', 'antigravity', 'opencode'])

    const detail = await fetch(new URL(`/_local/usage/session?agent=claude&native_session_id=${SESSION}`, host), { headers })
    expect(detail.status).toBe(200)
    expect(await detail.json()).toMatchObject({ schema: USAGE_SCHEMA, agent: 'claude', native_session_id: SESSION, totals: { turns: 2 } })
  })

  it('refuses unknown parameters, an unknown agent and a non-anchored session id', async () => {
    const home = await claudeHome()
    const service = createUsageService({ home })
    const core = await fakeCore()
    const host = await listen((req, res) => { void serveLocalUsage(req, res, core.origin, service) })
    const headers = { Authorization: 'Bearer valid' }

    const refused = async (path: string) => {
      const response = await fetch(new URL(path, host), { headers })
      return { status: response.status, body: (await response.json()) as { error: string; detail?: string } }
    }
    // A request can never narrow (or widen) the scan by naming a path of its own.
    expect(await refused('/_local/usage?root=/etc')).toEqual({ status: 400, body: { error: 'invalid_query_parameter', detail: 'root' } })
    expect(await refused('/_local/usage/session?agent=claude')).toEqual({ status: 400, body: { error: 'missing_native_session_id' } })
    expect(await refused('/_local/usage/session?agent=vim&native_session_id=abc')).toEqual({ status: 400, body: { error: 'invalid_agent' } })
    expect(await refused('/_local/usage/session?agent=claude&native_session_id=../../etc/passwd')).toEqual({ status: 400, body: { error: 'invalid_native_session_id' } })
    expect((await refused('/_local/usage/session?agent=claude&native_session_id=does-not-exist')).status).toBe(404)
    expect(await refused('/_local/usage/session?agent=claude&native_session_id=a&agent=codex')).toEqual({ status: 400, body: { error: 'duplicate_query_parameter', detail: 'agent' } })
    // Anything under the usage surface belongs to this handler and is refused here, not
    // answered by the static host.
    expect(await refused('/_local/usage/unknown')).toEqual({ status: 404, body: { error: 'usage_path_not_found' } })
    expect(await refused('/_local/usage/session/extra?agent=claude&native_session_id=a')).toEqual({ status: 404, body: { error: 'usage_path_not_found' } })
  })

  it('refuses a method other than GET', async () => {
    const home = await claudeHome()
    const service = createUsageService({ home })
    const core = await fakeCore()
    const host = await listen((req, res) => { void serveLocalUsage(req, res, core.origin, service) })
    const response = await fetch(new URL('/_local/usage', host), { method: 'POST', headers: { Authorization: 'Bearer valid' } })
    expect(response.status).toBe(405)
    expect(response.headers.get('allow')).toBe('GET')
    expect(await response.json()).toEqual({ error: 'method_not_allowed' })
  })

  it('is routed by the dashboard server itself', async () => {
    const home = await claudeHome()
    const bundle = await makeHome()
    await put(bundle, 'index.html', '<!doctype html><title>bundle</title>')
    const service = createUsageService({ home })
    const core = await fakeCore()
    const server = createDashboardServer({ root: bundle, origin: core.origin, usage: service })
    servers.push(server)
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const host = new URL(`http://127.0.0.1:${(server.address() as { port: number }).port}`)

    expect(isUsagePath('/_local/usage')).toBe(true)
    expect(isUsagePath('/_local/usage/session')).toBe(true)
    // A sub-path is still ours: the handler refuses it instead of serving the shell.
    expect(isUsagePath('/_local/usage/other')).toBe(true)
    expect(isUsagePath('/_local/usagez')).toBe(false)
    const response = await fetch(new URL('/_local/usage', host), { headers: { Authorization: 'Bearer valid' } })
    expect(response.status).toBe(200)
    expect(((await response.json()) as { schema: string }).schema).toBe(USAGE_SCHEMA)
    const missing = await fetch(new URL('/_local/usage/session?agent=codex&native_session_id=nope', host), { headers: { Authorization: 'Bearer valid' } })
    expect(missing.status).toBe(404)
    expect(await missing.json()).toEqual({ error: 'usage_session_not_found' })
  })

  it('anchors the session id pattern', () => {
    expect(USAGE_NATIVE_SESSION_ID.test(SESSION)).toBe(true)
    expect(USAGE_NATIVE_SESSION_ID.test('session-abc_1.2:3%4')).toBe(true)
    for (const bad of ['../../etc/passwd', 'a/b', 'a b', '', 'x'.repeat(201)]) expect(USAGE_NATIVE_SESSION_ID.test(bad)).toBe(false)
  })
})

describe('usage refresh and bounds', () => {
  const codexPath = () => `.codex/sessions/2026/02/01/rollout-2026-02-01T10-00-00-${SESSION}.jsonl`
  it('discovers sessions through detail after TTL and preserves cached scan diagnostics', async () => {
    const home = await makeHome()
    let now = 0
    const service = createUsageService({ home, now: () => now, limits: { cacheTtlMs: 10 } })
    expect(await service.readUsageSession('codex', SESSION)).toBeNull()
    await putJsonl(home, codexPath(), [
      '{bad json}',
      { type: 'token_usage_record', payload: { turn_id: 't1', usage: { input_tokens: 10 } } },
    ])
    now = 11
    expect((await service.readUsageSession('codex', SESSION))?.totals.total_tokens).toBe(10)
    const warnings = (await service.readUsageInventory()).warnings
    expect(warnings.some(warning => warning.includes('malformed'))).toBe(true)
    now = 22
    expect((await service.readUsageInventory()).warnings).toEqual(warnings)
  })

  it('reports omitted detail rows while retaining the observed response count', async () => {
    const home = await claudeHome()
    const detail = await createUsageService({ home, limits: { maxDetailTurns: 1 } }).readUsageSession('claude', SESSION)
    expect(detail?.turns).toHaveLength(1)
    expect(detail?.totals.turns).toBe(2)
    expect(detail?.warnings.some(warning => warning.includes('Turn detail limit'))).toBe(true)
  })

  it('counts responses within their own day and model buckets', async () => {
    const home = await makeHome()
    await putJsonl(home, codexPath(), [
      { type: 'turn_context', payload: { turn_id: 't1', model: 'model-a' } },
      { type: 'token_usage_record', timestamp: '2026-01-01T12:00:00Z', payload: { turn_id: 't1', usage: { input_tokens: 10 } } },
      { type: 'turn_context', payload: { turn_id: 't2', model: 'model-b' } },
      { type: 'token_usage_record', timestamp: '2026-01-03T12:00:00Z', payload: { turn_id: 't2', usage: { input_tokens: 20 } } },
    ])
    const inventory = await createUsageService({ home }).readUsageInventory()
    expect(inventory.by_day.map(row => row.turns)).toEqual([1, 1])
    expect(inventory.by_model.map(row => row.turns)).toEqual([1, 1])
  })
})

describe('remaining detail reader turn limits', () => {
  it.each(['dsh', 'grok', 'antigravity', 'opencode'] as const)(
    '%s retains full totals and response count while warning about omitted rows', async (agent) => {
      const home = await makeHome()
      const dshText = [
        { type: 'session', version: 4, id: `session-${SESSION}`, cwd: '/work/app' },
        ...[1, 2].map(turn => ({ type: 'assistant/message', data: {
          turn, step: 1, message: { id: `msg-${turn}`, source: { kind: 'model', model: 'test-model' } },
          usage: { inputTokens: 10, outputTokens: 5 },
        } })),
      ].map(row => JSON.stringify(row)).join('\n') + '\n'
      if (agent === 'dsh') {
        await put(home, `.dsh/sessions/--work-app--/session-${SESSION}/session.v4.jsonl.zstd`, 'compressed-placeholder')
      } else if (agent === 'grok') {
        await put(home, `.grok/sessions/%2Fwork%2Fapp/${SESSION}/usage.json`, {
          session: { inputTokens: 20, outputTokens: 10, turnCount: 2 },
          turns: [1, 2].map(turnNumber => ({ turnNumber, inputTokens: 10, outputTokens: 5 })),
        })
      } else if (agent === 'antigravity') {
        await putJsonl(home, `.gemini/antigravity/brain/${SESSION}/.system_generated/logs/transcript_full.jsonl`,
          [1, 2].map(step_index => ({ step_index, input_tokens: 10, output_tokens: 5 })))
      } else {
        await put(home, '.local/share/opencode/opencode.db', 'sqlite-placeholder')
      }
      const { run } = fakeCommands({
        zstd: () => dshText,
        sqlite: (_database, query) => {
          if (query.includes('select id, data')) return [1, 2].map(index => ({
            id: `msg-${index}`, data: JSON.stringify({ tokens: { input: 10, output: 5 } }),
          }))
          if (query.includes('from message')) return [{ session_id: SESSION, turns: 2 }]
          if (query.includes('from session')) return [{ id: SESSION, tokens_input: 20, tokens_output: 10 }]
          return []
        },
      })
      for (const maxDetailTurns of [1, 2]) {
        const service = createUsageService({ home, run, limits: { maxDetailTurns } })
        const nativeSessionId = agent === 'dsh' ? `session-${SESSION}` : SESSION
        const detail = await service.readUsageSession(agent, nativeSessionId)
        expect(detail?.totals.total_tokens).toBe(30)
        expect(detail?.totals.turns).toBe(2)
        expect(detail?.turns).toHaveLength(maxDetailTurns)
        expect(detail?.warnings.some(warning => warning.includes('Turn detail limit'))).toBe(maxDetailTurns === 1)
      }
    },
  )
})
