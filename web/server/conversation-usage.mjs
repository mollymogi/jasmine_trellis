/**
 * Conversation token usage for the dashboard's own panel (`/_local/usage`).
 *
 * Core records what an Agent *said* and deliberately records no billing metadata, so
 * token usage can only come from each Agent's own session files on this machine. This
 * module is the dashboard-local reader for that data. `src/api/usage.ts` is the frozen
 * client contract it satisfies, and `USAGE_AGENTS` here is the same list.
 *
 * The rules this file exists to keep:
 *
 * - Read-only. Nothing here writes, no Agent is run, and no filesystem path is ever
 *   taken from an HTTP request: the server fixes the roots, a request may name only an
 *   `agent` from the enum and a `native_session_id` matching
 *   {@link USAGE_NATIVE_SESSION_ID}, and that id is resolved through the index a scan
 *   built — never joined onto a path.
 * - A gap is data. A root that is absent is `missing`; a root that exists but cannot be
 *   read (no `zstd`, no `sqlite3`, an unreadable database) is `error`; both carry a
 *   `detail` string the UI shows instead of an empty total that would read as "you spent
 *   nothing". Anything skipped mid-scan becomes a `warnings[]` entry.
 * - Bounded. Every source has a per-file byte cap, a newest-N session cap, a per-agent
 *   byte budget and a small concurrency limit. The whole inventory is cached for
 *   {@link DEFAULTS.cacheTtlMs}, and after that each session remembers the signature of
 *   the files it was built from, so a re-scan on a machine with a gigabyte of logs only
 *   re-parses sessions that actually changed. A half-written trailing line, a `*.lock`
 *   sibling, a truncated record or an unreadable file is skipped and reported, never
 *   thrown.
 * - No prompt text. Only counters, timestamps, the model id and the working directory an
 *   Agent recorded for itself are read out; message content, environment variables,
 *   credentials and unrelated paths never enter a payload.
 *
 * ## Token convention
 *
 * The contract has five counters plus `total_tokens`. Every source is normalised onto one
 * convention so a total means the same thing in every row:
 *
 *   input_tokens        prompt tokens billed at the full rate (cache reads excluded)
 *   cached_read_tokens  prompt tokens served from a prompt cache
 *   cache_write_tokens  prompt tokens written into a prompt cache
 *   output_tokens       generated tokens (reasoning tokens are a *subset* of these)
 *   reasoning_tokens    the thinking/reasoning part of `output_tokens`, informational
 *   total_tokens        input + cached_read + cache_write + output
 *
 * Sources disagree about whether their own `input` already contains cache reads: Grok's
 * `inputTokens` and OpenCode's per-message `tokens.input` do, while Claude, DSH,
 * Antigravity and OpenCode's session columns keep the cache counts apart. Each scanner
 * normalises its source and says so where it does, and `total_tokens` is always the four
 * counters above added together — never a number copied from a product's own total, which
 * is how Codex's cumulative records and Grok's totals are cross-checked below.
 *
 * Two consequences worth knowing when comparing against a product's own UI:
 *
 * - Grok's published `totalTokens` is `input + output` and omits cache-creation, so a
 *   Grok row here can be a few hundred tokens above Grok's own total.
 * - OpenCode bills reasoning in a bucket of its own, so folding it into `output_tokens`
 *   makes this agent's total equal the sum of OpenCode's per-message totals
 *   (1,433,688,840 on this machine) rather than the four-column rollup (0.16 % lower).
 *
 * ## What each source can report
 *
 * | agent       | tokens | turns               | model | project        | cost_usd              |
 * | ----------- | ------ | ------------------- | ----- | -------------- | --------------------- |
 * | claude      | yes    | per API response    | yes   | line `cwd`     | `cost-state` snapshot |
 * | codex       | yes    | per API response    | yes   | `session_meta` | never                 |
 * | dsh         | yes    | per `assistant/message` | yes | session header | never                |
 * | grok        | yes    | per turn            | yes   | decoded dir    | `costUsdTicks`/1e9    |
 * | antigravity | yes    | per planner step    | no    | summaries DB   | never                 |
 * | opencode    | yes    | per assistant msg   | yes   | `directory`    | session `cost`        |
 *
 * `turns` therefore counts the rows of the per-turn series the source exposes: one row
 * per model response for Claude/Codex/DSH/Antigravity/OpenCode, one row per conversation
 * turn for Grok (which aggregates the calls inside a turn). `native_turn_id` is the
 * source's own id where one exists (Claude's resolving user `promptId`, Codex `turn_id`,
 * DSH message id, OpenCode message id). Antigravity's transcript has no uuid of its own,
 * so it uses the `step-<step_index>` convention Core writes for the same conversation;
 * Grok's ids live only in files this reader does not touch, so its turns stay null and
 * Core correlation falls back to timestamps. Only a source that records cost per unit
 * smaller than a session leaves per-turn cost unknown — the contract has no per-turn
 * `cost_usd` for that reason.
 */

import { spawn } from 'node:child_process'
import { createReadStream, promises as fs } from 'node:fs'
import http from 'node:http'
import { homedir } from 'node:os'
import { basename, dirname, join, relative, sep } from 'node:path'

/** The contract's schema tag. `src/api/usage.ts` re-exports the same literal. */
export const USAGE_SCHEMA = 'jasmine-core/conversation-usage@1'

/** Capture adapters, the local endpoints and the UI share this vocabulary. */
export const USAGE_AGENTS = Object.freeze(['claude', 'codex', 'dsh', 'grok', 'antigravity', 'opencode'])

/**
 * Anchored shape of a native session id, checked before any lookup.
 *
 * Every source's id is a UUID, `session-<uuid>` (DSH) or percent-encoded text (Grok), so
 * the character set stays small on purpose: no slash, no quote, no `..` — nothing that
 * could be spliced into a path or a SQL literal.
 */
export const USAGE_NATIVE_SESSION_ID = /^[A-Za-z0-9._%:-]{1,200}$/

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]'])

/** Bounds. Chosen so a cold inventory on this machine stays in seconds, not minutes. */
const DEFAULTS = Object.freeze({
  cacheTtlMs: 60_000,
  maxSessionsPerAgent: 400,
  maxDetailTurns: 5000,
  zstdTimeoutMs: 20_000,
  sqliteTimeoutMs: 8_000,
})

/**
 * Per-agent byte budget for one inventory.
 *
 * The budget bounds a single scan, not the machine: Claude Code is 1.5 GB of transcript
 * here and Codex 3.6 GB, but Codex is answerable from a 1.1 MB tail per session while a
 * Claude total needs the file. Sizing each budget to its source keeps a cold scan near a
 * few seconds and lets the per-session mtime cache make every later scan cheap.
 */
const BYTE_BUDGETS = Object.freeze({
  claude: 2560 * 1024 * 1024,
  codex: 192 * 1024 * 1024,
  dsh: 512 * 1024 * 1024,
  grok: 32 * 1024 * 1024,
  antigravity: 256 * 1024 * 1024,
  opencode: 0,
})

/** Per-source caps. A truncated read always becomes a warning, never a failure. */
const CAPS = Object.freeze({
  claude: { file: 128 * 1024 * 1024, detailFile: 320 * 1024 * 1024, subagentFile: 8 * 1024 * 1024, tail: 256 * 1024, head: 64 * 1024 },
  codex: { fullRead: 8 * 1024 * 1024, detailRead: 256 * 1024 * 1024, head: 64 * 1024, tail: 1024 * 1024 },
  dsh: { stream: 32 * 1024 * 1024, detailStream: 256 * 1024 * 1024 },
  grok: { file: 4 * 1024 * 1024 },
  antigravity: { file: 32 * 1024 * 1024, detailFile: 64 * 1024 * 1024 },
})

const emptyTokens = () => ({
  input_tokens: 0,
  cached_read_tokens: 0,
  cache_write_tokens: 0,
  output_tokens: 0,
  reasoning_tokens: 0,
  total_tokens: 0,
})

/** Record one observed response in an internal breakdown bucket. */
function addResponse(into, delta) {
  addTokens(into, delta)
  into.responseCount = (into.responseCount ?? 0) + 1
}

/** Get-or-create the token accumulator stored under `key` in `map`. */
function tokensFor(map, key) {
  let tokens = map.get(key)
  if (!tokens) {
    tokens = emptyTokens()
    map.set(key, tokens)
  }
  return tokens
}

/** Add one normalised delta into an accumulator. `total_tokens` is always derived. */
function addTokens(into, delta) {
  into.input_tokens += delta.input_tokens ?? 0
  into.cached_read_tokens += delta.cached_read_tokens ?? 0
  into.cache_write_tokens += delta.cache_write_tokens ?? 0
  into.output_tokens += delta.output_tokens ?? 0
  into.reasoning_tokens += delta.reasoning_tokens ?? 0
  into.total_tokens = into.input_tokens + into.cached_read_tokens + into.cache_write_tokens + into.output_tokens
  return into
}

/** Finish a delta whose `total_tokens` follows the normalisation rule. */
/**
 * A turn row per the frozen contract: `UsageTurn extends UsageTokens`, so the five counters
 * sit beside the four identity fields instead of nested under a `tokens` key (a turn is the
 * only usage row that carries both, and the contract keeps it flat).
 */
function turnRow(identity, delta) {
  return { ...completeDelta(delta), ...identity }
}

function completeDelta(delta) {
  delta.total_tokens = delta.input_tokens + delta.cached_read_tokens + delta.cache_write_tokens + delta.output_tokens
  return delta
}

/**
 * Prompt tokens that could have been served from cache, i.e. the denominator of
 * `cache_hit_ratio`. `null` when nothing prompt-side was billed — the contract's
 * "unknown", not zero.
 */
function cacheHitRatio(tokens) {
  const denominator = tokens.input_tokens + tokens.cached_read_tokens + tokens.cache_write_tokens
  return denominator > 0 ? tokens.cached_read_tokens / denominator : null
}

/** `YYYY-MM-DD` in this machine's zone: the contract asks for a local calendar day. */
function localDay(value) {
  if (value === null || value === undefined || value === '') return null
  const ms = typeof value === 'number' ? value : Date.parse(value)
  if (!Number.isFinite(ms)) return null
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return null
  const pad = (part) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** ISO stamp or null. An unparsable stamp is dropped rather than passed through raw. */
function isoOrNull(value) {
  if (value === null || value === undefined || value === '') return null
  const ms = typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value) : Number.NaN
  if (!Number.isFinite(ms)) return null
  return new Date(ms).toISOString()
}

function numberOrZero(value) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0
}

/** Root label as `~/.claude/projects`: home-relative, never an absolute path. */
function rootLabel(root, home) {
  if (!root) return ''
  if (relative(home, root) === '') return '~'
  const rel = relative(home, root)
  return rel.startsWith('..') || rel.startsWith(sep) ? root : `~/${rel.split(sep).join('/')}`
}

async function statSafe(path) {
  try {
    return await fs.stat(path)
  } catch {
    return null
  }
}

async function readdirSafe(path) {
  try {
    return await fs.readdir(path, { withFileTypes: true })
  } catch {
    return null
  }
}

/**
 * Every regular file under `dir` that passes `filter`.
 *
 * Symlinks are skipped rather than followed: a session root must not become a way to
 * read an arbitrary file the operator happens to have linked into it.
 */
async function walkFiles(dir, { maxDepth = 6, maxEntries = 4000, filter = () => true } = {}) {
  const found = []
  const stack = [[dir, 0]]
  while (stack.length > 0 && found.length < maxEntries) {
    const [current, depth] = stack.pop()
    const entries = await readdirSafe(current)
    if (!entries) continue
    for (const entry of entries) {
      if (found.length >= maxEntries) break
      if (entry.isSymbolicLink()) continue
      const full = join(current, entry.name)
      if (entry.isDirectory()) {
        if (depth < maxDepth) stack.push([full, depth + 1])
        continue
      }
      if (!entry.isFile() || !filter(entry.name, full)) continue
      const info = await statSafe(full)
      if (info?.isFile()) found.push({ path: full, name: entry.name, size: info.size, mtimeMs: info.mtimeMs })
    }
  }
  return found
}

/**
 * Feed the decoded lines of an async byte stream to `onLine`.
 *
 * Splitting happens on bytes, so a multi-byte UTF-8 character straddling two chunks
 * cannot corrupt a record. A trailing fragment without a newline is a record the writer
 * had not finished flushing: it is handed over only when the stream ended normally, and a
 * `JSON.parse` failure there counts as a bad line instead of throwing.
 */
async function forEachLine(stream, { maxBytes = Infinity, onLine } = {}) {
  let carry = Buffer.alloc(0)
  let bytes = 0
  let lines = 0
  let badLines = 0
  let truncated = false
  const emit = (buffer) => {
    if (buffer.length === 0) return
    lines += 1
    let record
    try {
      record = JSON.parse(buffer.toString('utf8'))
    } catch {
      badLines += 1
      return
    }
    onLine(record)
  }
  try {
    for await (const chunk of stream) {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
      if (bytes + buffer.length > maxBytes) {
        const room = Math.max(0, maxBytes - bytes)
        if (room > 0) {
          const slice = buffer.subarray(0, room)
          carry = carry.length > 0 ? Buffer.concat([carry, slice]) : slice
        }
        bytes += room
        truncated = true
        break
      }
      bytes += buffer.length
      carry = carry.length > 0 ? Buffer.concat([carry, buffer]) : buffer
      let newline = carry.indexOf(10)
      while (newline >= 0) {
        emit(carry.subarray(0, newline))
        carry = carry.subarray(newline + 1)
        newline = carry.indexOf(10)
      }
    }
  } catch {
    // A file that disappears mid-read (rotation, agent shutdown) ends the scan quietly.
    truncated = true
  }
  if (!truncated) emit(carry)
  return { bytes, lines, badLines, truncated }
}

/**
 * Spawn a helper binary and collect its stdout under a hard timeout.
 *
 * `zstd` and `sqlite3` are the only external programs this module runs. Both are resolved
 * by name with a fixed argv, so nothing from a request can change what is executed. On
 * timeout the child is killed and the caller treats partial output as partial success.
 */
function runCommand(command, args, { timeoutMs = 10_000, maxBytes = 8 * 1024 * 1024 } = {}) {
  return new Promise((resolve) => {
    let settled = false
    const finish = (result) => {
      if (settled) return
      settled = true
      resolve(result)
    }
    let child
    try {
      child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], env: process.env })
    } catch (error) {
      finish({ ok: false, code: null, stdout: '', stderr: '', error, truncated: false })
      return
    }
    const chunks = []
    let bytes = 0
    let truncated = false
    const timer = setTimeout(() => {
      truncated = true
      child.kill('SIGKILL')
    }, timeoutMs)
    timer.unref?.()
    child.stdout.on('data', (chunk) => {
      if (bytes >= maxBytes) return
      const room = maxBytes - bytes
      const slice = chunk.length > room ? chunk.subarray(0, room) : chunk
      chunks.push(slice)
      bytes += slice.length
      if (slice.length < chunk.length) truncated = true
    })
    // Nothing reads stderr, but it must be drained or a chatty child blocks on the pipe.
    child.stderr.resume()
    child.on('error', (error) => {
      clearTimeout(timer)
      finish({ ok: false, code: null, stdout: '', stderr: '', error, truncated })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      finish({ ok: code === 0 || (truncated && bytes > 0), code, stdout: Buffer.concat(chunks).toString('utf8'), stderr: '', truncated })
    })
  })
}

/** First candidate that answers `--version` wins; `null` means the helper is absent. */
async function resolveBinary(ctx, candidates) {
  for (const candidate of candidates) {
    const probe = await ctx.run(candidate, ['--version'], { timeoutMs: 4000, maxBytes: 4096 })
    if (probe.ok) return candidate
  }
  return null
}

/** Read `[start,end)` of a file without loading the rest of it. */
async function readSlice(path, start, end) {
  let handle
  try {
    handle = await fs.open(path, 'r')
    const length = Math.max(0, end - start)
    if (length === 0) return ''
    const buffer = Buffer.alloc(length)
    const { bytesRead } = await handle.read(buffer, 0, length, start)
    return buffer.subarray(0, bytesRead).toString('utf8')
  } catch {
    return null
  } finally {
    if (handle) await handle.close().catch(() => {})
  }
}

function emptySource(agent, root) {
  return { agent, root, status: 'ok', sessions: 0 }
}

/** Files that carry one session, as a stable identity for the mtime cache. */
function fileSignature(files) {
  return files.map((file) => `${file.path}:${file.mtimeMs}:${file.size}`).join('|')
}

/**
 * The usage service: roots, clock, cache and resolved helper binaries for one dashboard
 * server.
 *
 * `roots` is injectable for tests and for a home other than the default. Every value is an
 * absolute directory, except OpenCode's, which is its database file; `antigravity` and
 * `antigravitySummaries` accept lists because the product keeps one home per front-end
 * (app, CLI, IDE) and all of them write transcripts. `run` is the command runner used for
 * `zstd`/`sqlite3`, injectable so a test can supply both without the binaries.
 */
export function createUsageService({ home = homedir(), now = () => Date.now(), roots = {}, limits = {}, run = runCommand } = {}) {
  const bounds = { ...DEFAULTS, ...limits }
  const resolved = {
    claude: join(home, '.claude', 'projects'),
    codex: join(home, '.codex', 'sessions'),
    codexArchived: join(home, '.codex', 'archived_sessions'),
    dsh: join(home, '.dsh', 'sessions'),
    grok: join(home, '.grok', 'sessions'),
    antigravity: [
      join(home, '.gemini', 'antigravity', 'brain'),
      join(home, '.gemini', 'antigravity-cli', 'brain'),
      join(home, '.gemini', 'antigravity-ide', 'brain'),
    ],
    antigravitySummaries: [
      join(home, '.gemini', 'antigravity', 'conversation_summaries.db'),
      join(home, '.gemini', 'antigravity-cli', 'conversation_summaries.db'),
      join(home, '.gemini', 'antigravity-ide', 'conversation_summaries.db'),
    ],
    opencode: join(home, '.local', 'share', 'opencode', 'opencode.db'),
    ...roots,
  }
  if (typeof resolved.antigravity === 'string') resolved.antigravity = [resolved.antigravity]
  if (typeof resolved.antigravitySummaries === 'string') resolved.antigravitySummaries = [resolved.antigravitySummaries]

  const state = {
    inventory: null,
    inventoryAt: 0,
    /** agent -> native session id -> the session entry a detail read may use. */
    index: new Map(),
    /** `${agent}\u0000${id}` -> {signature,row}: the mtime cache across inventory scans. */
    rows: new Map(),
    /** Whole-agent reuse for sources whose only file is one database. */
    snapshots: new Map(),
    detail: new Map(),
    zstd: undefined,
    sqlite: undefined,
  }
  const ctx = { home, now, roots: resolved, bounds, run, state }
  return {
    agents: USAGE_AGENTS,
    readUsageInventory: () => readUsageInventory(ctx),
    readUsageSession: (agent, nativeSessionId) => readUsageSession(ctx, agent, nativeSessionId),
    /** Drop every cached value; used by tests and by an operator-facing refresh. */
    invalidate: () => {
      state.inventory = null
      state.inventoryAt = 0
      state.index = new Map()
      state.rows.clear()
      state.snapshots.clear()
      state.detail.clear()
    },
  }
}

/* -------------------------------------------------------------------------- */
/* Claude Code — ~/.claude/projects/<encoded-project>/<session-uuid>.jsonl     */
/* -------------------------------------------------------------------------- */

/**
 * Claude Code appends one JSONL file per session, plus `<uuid>/subagents/*.jsonl` for
 * delegated work and `isSidechain: true` lines inside the parent file. A session's usage
 * is its parent file plus every subagent file: the CLI writes subagent usage only there
 * (verified on real data: zero `uuid` overlap between a parent file and its subagent
 * files), so adding them is not double counting.
 *
 * Two format facts this parser depends on, both verified against real files:
 *
 * - An assistant message may be written more than once (once per content block, and again
 *   on resume) with byte-identical `usage`. Summing lines would roughly double a session,
 *   so usage is deduplicated by `message.id`; a duplicate still contributes its `uuid` to
 *   the parent chain, it just adds no tokens.
 * - `cost-state` records carry the CLI's own running `totalCostUSD` and a per-model token
 *   snapshot. The snapshot lags the transcript and does not match a line sum, so it is
 *   used only for `cost_usd` — and, for a file beyond the read cap, as the labelled
 *   fallback total described in `scanClaude`.
 *
 * The project path is the line's own `cwd`, which is authoritative. The directory name is
 * never decoded: the encoding is lossy, since "/" and " " both become "-".
 */
async function discoverClaude(ctx) {
  const root = ctx.roots.claude
  const source = emptySource('claude', rootLabel(root, ctx.home))
  const projects = await readdirSafe(root)
  if (!projects) {
    return { source: { ...source, status: 'missing', detail: 'no Claude Code session root on this machine' }, sessions: [], skipped: 0 }
  }
  const files = []
  for (const project of projects) {
    if (!project.isDirectory() || project.isSymbolicLink()) continue
    const projectDir = join(root, project.name)
    for (const file of await walkFiles(projectDir, { maxDepth: 0, filter: (name) => name.endsWith('.jsonl') })) {
      files.push({ ...file, projectName: project.name, sessionId: basename(file.name, '.jsonl'), subagent: false })
    }
    for (const session of (await readdirSafe(projectDir)) ?? []) {
      if (!session.isDirectory() || session.isSymbolicLink()) continue
      const subagents = join(projectDir, session.name, 'subagents')
      for (const file of await walkFiles(subagents, { maxDepth: 0, filter: (name) => name.endsWith('.jsonl') })) {
        files.push({ ...file, projectName: project.name, sessionId: session.name, subagent: true })
      }
    }
  }
  const grouped = new Map()
  for (const file of files) {
    if (!USAGE_NATIVE_SESSION_ID.test(file.sessionId)) continue
    const group = grouped.get(file.sessionId) ?? { id: file.sessionId, files: [] }
    // The parent file first, so its `cwd` wins over a subagent file's inherited one.
    if (file.subagent) group.files.push(file)
    else group.files.unshift(file)
    grouped.set(file.sessionId, group)
  }
  const all = [...grouped.values()].sort((a, b) => newestMtime(b.files) - newestMtime(a.files))
  const kept = all.slice(0, ctx.bounds.maxSessionsPerAgent)
  return { source, sessions: kept, skipped: all.length - kept.length }
}

const newestMtime = (files) => files.reduce((newest, file) => Math.max(newest, file.mtimeMs), 0)

/**
 * Sum one Claude session's files.
 *
 * `promptByUuid`/`parentByUuid` are built as the file streams so an assistant line can
 * name the user turn that prompted it (assistant lines carry no `promptId`). The walk is
 * capped at five hops: a deeper chain is a compacted transcript whose prompt is gone, and
 * `null` is the honest answer there.
 */
async function scanClaudeGroup(ctx, group, { keepTurns }) {
  const result = {
    project: null,
    models: new Set(),
    modelTokens: new Map(),
    tokens: emptyTokens(),
    turns: [],
    turnCount: 0,
    startedAt: null,
    lastAt: null,
    costUsd: null,
    days: new Map(),
    badLines: 0,
    partial: false,
  }
  const seenMessageIds = new Set()
  const promptByUuid = new Map()
  const parentByUuid = new Map()

  const observe = (at) => {
    if (!at) return
    if (result.startedAt === null || at < result.startedAt) result.startedAt = at
    if (result.lastAt === null || at > result.lastAt) result.lastAt = at
  }

  const onLine = (record) => {
    if (typeof record?.uuid === 'string') {
      if (typeof record.promptId === 'string') promptByUuid.set(record.uuid, record.promptId)
      if (typeof record.parentUuid === 'string') parentByUuid.set(record.uuid, record.parentUuid)
    }
    if (result.project === null && typeof record?.cwd === 'string') result.project = record.cwd
    const at = isoOrNull(record?.timestamp)
    observe(at)
    if (record?.type === 'cost-state' && Number.isFinite(record.totalCostUSD)) {
      // A running total: the last record in the file is the session's final estimate.
      result.costUsd = record.totalCostUSD
    }
    const usage = record?.type === 'assistant' ? record.message?.usage : null
    if (!usage) return
    const messageId = typeof record.message?.id === 'string' ? record.message.id : null
    if (messageId !== null) {
      if (seenMessageIds.has(messageId)) return
      seenMessageIds.add(messageId)
    }
    const model = typeof record.message?.model === 'string' ? record.message.model : 'unknown'
    const delta = completeDelta({
      // These case-insensitive counters are disjoint, so `input` needs no adjustment.
      input_tokens: numberOrZero(usage.input_tokens),
      cached_read_tokens: numberOrZero(usage.cache_read_input_tokens),
      cache_write_tokens: numberOrZero(usage.cache_creation_input_tokens),
      output_tokens: numberOrZero(usage.output_tokens),
      reasoning_tokens: numberOrZero(usage.output_tokens_details?.thinking_tokens),
    })
    addTokens(result.tokens, delta)
    addResponse(tokensFor(result.modelTokens, model), delta)
    result.models.add(model)
    result.turnCount += 1
    const day = localDay(at)
    if (day) addResponse(tokensFor(result.days, day), delta)
    if (keepTurns && result.turns.length < ctx.bounds.maxDetailTurns) {
      result.turns.push(
        turnRow(
          {
            index: result.turns.length + 1,
            native_turn_id: resolveClaudePrompt(record.uuid, promptByUuid, parentByUuid),
            step: null,
            at,
            model,
          },
          delta,
        ),
      )
    }
  }

  for (const file of group.files) {
    const cap = keepTurns ? CAPS.claude.detailFile : file.subagent ? CAPS.claude.subagentFile : CAPS.claude.file
    const read = await forEachLine(createReadStream(file.path), { maxBytes: cap, onLine })
    if (read.truncated) result.partial = true
    result.badLines += read.badLines
  }
  return result
}

function resolveClaudePrompt(uuid, promptByUuid, parentByUuid) {
  if (typeof uuid !== 'string') return null
  let cursor = uuid
  for (let hop = 0; hop < 6 && cursor; hop += 1) {
    const owner = promptByUuid.get(cursor)
    if (owner) return owner
    cursor = parentByUuid.get(cursor)
  }
  return null
}

/** The last `cost-state` record of a Claude session, read from the file's tail only. */
async function readClaudeCostSnapshot(files) {
  for (const file of files) {
    if (file.subagent) continue
    const info = await statSafe(file.path)
    if (!info || info.size === 0) continue
    const tail = await readSlice(file.path, Math.max(0, info.size - CAPS.claude.tail), info.size)
    if (!tail) continue
    let snapshot = null
    for (const line of tail.split('\n')) {
      if (!line.includes('cost-state')) continue
      try {
        const record = JSON.parse(line)
        if (record?.type === 'cost-state' && record.modelUsage) snapshot = record
      } catch {
        // A tail begins mid-record; the fragment is expected, not a warning.
      }
    }
    if (snapshot) return snapshot
  }
  return null
}

function claudeRowFromSnapshot(snapshot) {
  const tokens = emptyTokens()
  const modelTokens = new Map()
  const models = new Set()
  for (const [model, counters] of Object.entries(snapshot.modelUsage ?? {})) {
    const delta = completeDelta({
      input_tokens: numberOrZero(counters.inputTokens),
      cached_read_tokens: numberOrZero(counters.cacheReadInputTokens),
      cache_write_tokens: numberOrZero(counters.cacheCreationInputTokens),
      output_tokens: numberOrZero(counters.outputTokens),
      reasoning_tokens: numberOrZero(counters.thinkingTokens),
    })
    addTokens(tokens, delta)
    addTokens(tokensFor(modelTokens, model), delta)
    models.add(model)
  }
  return { tokens, modelTokens, models }
}

async function scanClaude(ctx) {
  const { source, sessions, skipped } = await discoverClaude(ctx)
  if (source.status === 'missing') return { source, sessions: [], warnings: [] }
  const warnings = []
  if (skipped > 0) warnings.push(`claude: ${skipped} older session(s) beyond the newest ${ctx.bounds.maxSessionsPerAgent} were not read`)

  let budget = BYTE_BUDGETS.claude
  let truncated = 0
  let snapshotFallback = 0
  let dropped = 0
  let malformed = 0
  const rows = []

  for (const group of sessions) {
    const size = group.files.reduce((total, file) => total + file.size, 0)
    if (budget <= 0) {
      dropped += 1
      continue
    }
    budget -= size
    const key = `claude\u0000${group.id}`
    const signature = fileSignature(group.files)
    const cached = ctx.state.rows.get(key)
    if (cached?.signature === signature) {
      truncated += cached.diagnostics?.truncated ?? 0
      snapshotFallback += cached.diagnostics?.snapshotFallback ?? 0
      malformed += cached.diagnostics?.malformed ?? 0
      rows.push(cached.row)
      continue
    }
    const scanned = await scanClaudeGroup(ctx, group, { keepTurns: false })
    malformed += scanned.badLines
    let usedSnapshot = false
    let tokens = scanned.tokens
    let modelTokens = scanned.modelTokens
    let models = scanned.models
    let costUsd = scanned.costUsd
    if (scanned.partial) {
      truncated += 1
      // Beyond the read cap the driver still has its own cumulative snapshot in the last
      // kilobyte. It lags the transcript, so it replaces the partial sum only as a
      // labelled fallback: a stale total beats half a total, and the warning says so.
      const snapshot = await readClaudeCostSnapshot(group.files)
      if (snapshot) {
        snapshotFallback += 1
        usedSnapshot = true
        const fromSnapshot = claudeRowFromSnapshot(snapshot)
        tokens = fromSnapshot.tokens
        modelTokens = fromSnapshot.modelTokens
        models = fromSnapshot.models
        costUsd = Number.isFinite(snapshot.totalCostUSD) ? snapshot.totalCostUSD : costUsd
      }
    }
    const row = {
      agent: 'claude',
      id: group.id,
      files: group.files,
      project: scanned.project,
      models: [...models],
      modelTokens,
      tokens,
      turnCount: scanned.turnCount,
      costUsd,
      startedAt: scanned.startedAt,
      lastAt: scanned.lastAt,
      days: scanned.days,
    }
    ctx.state.rows.set(key, { signature, row, diagnostics: { truncated: Number(scanned.partial), snapshotFallback: Number(usedSnapshot), malformed: scanned.badLines } })
    rows.push(row)
  }

  if (truncated > 0) {
    warnings.push(
      `claude: ${truncated} session file(s) exceed the ${Math.round(CAPS.claude.file / (1024 * 1024))} MB read cap; ` +
        `${snapshotFallback} of them report the CLI's own cost snapshot instead of a line sum`,
    )
  }
  if (dropped > 0) warnings.push(`claude: ${dropped} session(s) skipped after the per-request byte budget was spent`)
  if (malformed > 0) warnings.push(`claude: ${malformed} malformed line(s) skipped`)
  return { source: { ...source, sessions: rows.length }, sessions: rows, warnings }
}

async function readClaudeDetail(ctx, entry) {
  const scanned = await scanClaudeGroup(ctx, entry, { keepTurns: true })
  return {
    project: scanned.project,
    models: [...scanned.models],
    startedAt: scanned.startedAt,
    lastAt: scanned.lastAt,
    tokens: scanned.tokens,
    turns: scanned.turns,
    turnCount: scanned.turnCount,
    warnings: [
      ...(scanned.badLines > 0 ? [`${scanned.badLines} malformed line(s) skipped`] : []),
      ...(scanned.partial ? ['Transcript byte read cap reached; totals cover only the readable prefix'] : []),
      ...(scanned.turnCount > scanned.turns.length ? ['Turn detail limit reached; some response rows are omitted'] : []),
    ],
  }
}

/* -------------------------------------------------------------------------- */
/* Codex — ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (+ archived_sessions)  */
/* -------------------------------------------------------------------------- */

/**
 * Codex writes one `rollout-*.jsonl` per conversation. Token accounting lives in
 * `token_usage_record` lines that carry three views of the same usage:
 *
 *   payload.usage              one API response
 *   payload.turn_token_usage   cumulative for that turn
 *   payload.thread_token_usage cumulative for the whole session
 *
 * Verified against real files: the sum of every `payload.usage` equals the last
 * `thread_token_usage` exactly (21,020,913 and 223,183,745 on two sessions), so the
 * session total is read from the *last* `thread_token_usage` — which is what makes a
 * 678 MB rollout answerable from its tail, without ever summing a cumulative counter.
 *
 * `input_tokens` here already contains `cached_input_tokens`, hence the subtraction.
 */
async function discoverCodex(ctx) {
  const root = ctx.roots.codex
  const source = emptySource('codex', rootLabel(root, ctx.home))
  if (!(await statSafe(root))) {
    return { source: { ...source, status: 'missing', detail: 'no Codex session root on this machine' }, sessions: [], skipped: 0 }
  }
  const isRollout = (name) => name.startsWith('rollout-') && name.endsWith('.jsonl')
  const files = await walkFiles(root, { maxDepth: 5, filter: isRollout })
  if (typeof ctx.roots.codexArchived === 'string' && (await statSafe(ctx.roots.codexArchived))) {
    files.push(...(await walkFiles(ctx.roots.codexArchived, { maxDepth: 5, filter: isRollout })))
  }
  const all = files
    .map((file) => {
      const match = /([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$/.exec(file.name)
      return { id: match?.[1] ?? basename(file.name, '.jsonl'), files: [file] }
    })
    .filter((entry) => USAGE_NATIVE_SESSION_ID.test(entry.id))
    .sort((a, b) => newestMtime(b.files) - newestMtime(a.files))
  const kept = all.slice(0, ctx.bounds.maxSessionsPerAgent)
  return { source, sessions: kept, skipped: all.length - kept.length }
}

function codexDelta(usage) {
  const cached = numberOrZero(usage?.cached_input_tokens)
  const cacheWrite = numberOrZero(usage?.cache_write_input_tokens)
  return completeDelta({
    // OpenAI-style counters: `input_tokens` already includes the cached part.
    input_tokens: Math.max(0, numberOrZero(usage?.input_tokens) - cached - cacheWrite),
    cached_read_tokens: cached,
    cache_write_tokens: cacheWrite,
    output_tokens: numberOrZero(usage?.output_tokens),
    reasoning_tokens: numberOrZero(usage?.reasoning_output_tokens),
  })
}

/**
 * One rollout. `keepTurns` (the detail path) streams the whole file for a per-response
 * series; the inventory path streams small files and falls back to a head+tail read for
 * anything past the full-read cap, where identity comes from the head and totals from the
 * last cumulative record in the tail.
 */
async function scanCodexEntry(ctx, entry, { keepTurns }) {
  const file = entry.files[0]
  const result = {
    project: null,
    models: new Set(),
    modelTokens: new Map(),
    tokens: emptyTokens(),
    turns: [],
    responses: 0,
    startedAt: null,
    lastAt: null,
    costUsd: null,
    days: new Map(),
    partial: false,
    badLines: 0,
    modelByTurn: new Map(),
  }
  const observe = (at) => {
    if (!at) return
    if (result.startedAt === null || at < result.startedAt) result.startedAt = at
    if (result.lastAt === null || at > result.lastAt) result.lastAt = at
  }
  const onLine = (record) => {
    if (record?.type === 'session_meta') {
      if (typeof record.payload?.cwd === 'string') result.project = record.payload.cwd
      observe(isoOrNull(record.payload?.timestamp ?? record.timestamp))
    }
    if (record?.type === 'turn_context') {
      if (typeof record.payload?.model === 'string') {
        result.models.add(record.payload.model)
        if (typeof record.payload?.turn_id === 'string') result.modelByTurn.set(record.payload.turn_id, record.payload.model)
      }
      return
    }
    if (record?.type !== 'token_usage_record') return
    const turnId = typeof record.payload?.turn_id === 'string' ? record.payload.turn_id : null
    const at = isoOrNull(record.timestamp)
    observe(at)
    result.responses += 1
    const model = (turnId && result.modelByTurn.get(turnId)) || 'unknown'
    if (turnId && model !== 'unknown') result.models.add(model)
    const delta = codexDelta(record.payload?.usage)
    addTokens(result.tokens, delta)
    addResponse(tokensFor(result.modelTokens, model), delta)
    const day = localDay(at)
    if (day) addResponse(tokensFor(result.days, day), delta)
    if (keepTurns && result.turns.length < ctx.bounds.maxDetailTurns) {
      result.turns.push(
        turnRow({ index: result.turns.length + 1, native_turn_id: turnId, step: null, at, model: model === 'unknown' ? null : model }, delta),
      )
    }
  }

  if (file.size <= CAPS.codex.fullRead || keepTurns) {
    const read = await forEachLine(createReadStream(file.path), {
      maxBytes: keepTurns ? CAPS.codex.detailRead : CAPS.codex.fullRead,
      onLine,
    })
    if (read.truncated) result.partial = true
    result.badLines = read.badLines
    result.turnCount = result.responses
    return result
  }

  // Large rollout: identity from the head, cumulative total from the tail.
  const head = await readSlice(file.path, 0, CAPS.codex.head)
  if (head) {
    for (const line of head.split('\n')) {
      if (!line.startsWith('{')) continue
      try {
        onLine(JSON.parse(line))
      } catch {
        // partial head record
      }
    }
  }
  const tailStart = Math.max(0, file.size - CAPS.codex.tail)
  const tail = await readSlice(file.path, tailStart, file.size)
  if (tail) {
    // Drop the record the slice started in the middle of.
    const text = tailStart > 0 ? tail.slice(tail.indexOf('\n') + 1) : tail
    let cumulative = null
    let lastAt = null
    for (const line of text.split('\n')) {
      if (!line.startsWith('{')) continue
      let record
      try {
        record = JSON.parse(line)
      } catch {
        continue
      }
      if (record?.type === 'turn_context' && typeof record.payload?.model === 'string') {
        result.models.add(record.payload.model)
      }
      if (record?.type !== 'token_usage_record') continue
      result.responses += 1
      const total = record.payload?.thread_token_usage
      if (total) cumulative = total
      lastAt = isoOrNull(record.timestamp) ?? lastAt
    }
    observe(lastAt)
    if (cumulative) {
      // Cumulative: assign, never add.
      result.tokens = codexDelta(cumulative)
    }
    if (cumulative) {
      const known = emptyTokens()
      for (const counters of result.modelTokens.values()) addTokens(known, counters)
      const remainder = emptyTokens()
      for (const field of Object.keys(remainder)) remainder[field] = Math.max(0, result.tokens[field] - known[field])
      const unknown = tokensFor(result.modelTokens, 'unknown')
      addTokens(unknown, remainder)
      unknown.responseCount = (unknown.responseCount ?? 0) + result.responses - [...result.modelTokens.values()].reduce((sum, bucket) => sum + (bucket.responseCount ?? 0), 0)
      result.models.add('unknown')
    }
  }
  result.partial = true
  // Only the tail window was read, so this counts the responses inside it: a lower bound
  // the warning for this session says is a lower bound.
  result.turnCount = result.responses
  return result
}

async function scanCodex(ctx) {
  const { source, sessions, skipped } = await discoverCodex(ctx)
  if (source.status === 'missing') return { source, sessions: [], warnings: [] }
  const warnings = []
  if (skipped > 0) warnings.push(`codex: ${skipped} older session(s) beyond the newest ${ctx.bounds.maxSessionsPerAgent} were not read`)

  let budget = BYTE_BUDGETS.codex
  let tailOnly = 0
  let dropped = 0
  let malformed = 0
  const rows = []
  for (const entry of sessions) {
    const size = entry.files[0].size
    if (budget <= 0) {
      dropped += 1
      continue
    }
    // A tail read costs head+tail, not the whole file.
    budget -= size <= CAPS.codex.fullRead ? size : CAPS.codex.head + CAPS.codex.tail
    const key = `codex\u0000${entry.id}`
    const signature = fileSignature(entry.files)
    const cached = ctx.state.rows.get(key)
    if (cached?.signature === signature) {
      tailOnly += cached.diagnostics?.tailOnly ?? 0
      malformed += cached.diagnostics?.malformed ?? 0
      rows.push(cached.row)
      continue
    }
    const scanned = await scanCodexEntry(ctx, entry, { keepTurns: false })
    malformed += scanned.badLines
    if (scanned.partial) tailOnly += 1
    const row = {
      agent: 'codex',
      id: entry.id,
      files: entry.files,
      project: scanned.project,
      models: [...scanned.models],
      modelTokens: scanned.modelTokens,
      tokens: scanned.tokens,
      turnCount: scanned.turnCount,
      costUsd: null,
      startedAt: scanned.startedAt,
      lastAt: scanned.lastAt,
      days: scanned.days,
    }
    ctx.state.rows.set(key, { signature, row, diagnostics: { tailOnly: Number(scanned.partial), malformed: scanned.badLines } })
    rows.push(row)
  }
  if (tailOnly > 0) {
    warnings.push(
      `codex: ${tailOnly} session(s) exceed the ${Math.round(CAPS.codex.fullRead / (1024 * 1024))} MB full-read cap; ` +
        'their totals come from the last cumulative record, their turn count from the tail window and their day bucket from the last activity',
    )
  }
  if (dropped > 0) warnings.push(`codex: ${dropped} session(s) skipped after the per-request byte budget was spent`)
  if (malformed > 0) warnings.push(`codex: ${malformed} malformed line(s) skipped`)
  return { source: { ...source, sessions: rows.length }, sessions: rows, warnings }
}

async function readCodexDetail(ctx, entry) {
  const scanned = await scanCodexEntry(ctx, entry, { keepTurns: true })
  return {
    project: scanned.project,
    models: [...scanned.models],
    startedAt: scanned.startedAt,
    lastAt: scanned.lastAt,
    tokens: scanned.tokens,
    turns: scanned.turns,
    turnCount: scanned.turnCount,
    warnings: [
      ...(scanned.badLines > 0 ? [`${scanned.badLines} malformed line(s) skipped`] : []),
      ...(scanned.partial ? ['Transcript byte read cap reached; totals cover only the readable prefix'] : []),
      ...(scanned.turnCount > scanned.turns.length ? ['Turn detail limit reached; some response rows are omitted'] : []),
    ],
  }
}

/* -------------------------------------------------------------------------- */
/* DSH — ~/.dsh/sessions/--<projectKey>--/session-<uuid>/session.v{3,4}.jsonl.zstd */
/* -------------------------------------------------------------------------- */

/**
 * DSH keeps one zstd-compressed JSONL per session; format v3 and v4 both exist and both
 * must be read. The header line is `{"type":"session","id":"session-<uuid>","cwd":...}`;
 * usage is on `assistant/message` records as `data.usage`
 * `{inputTokens, outputTokens, totalTokens, cacheReadTokens?, reasoningTokens?}` — v4 may
 * omit the last two, which default to zero. `totalTokens` is provided and equals
 * input+output+cacheRead exactly, so `inputTokens` is already uncached here.
 *
 * The decompressor is the system `zstd` binary (no npm dependency). If it is absent the
 * source becomes `error` with a detail string rather than an exception, and a truncated
 * frame keeps the records it did emit with a warning.
 */
async function discoverDsh(ctx) {
  const root = ctx.roots.dsh
  const source = emptySource('dsh', rootLabel(root, ctx.home))
  if (!(await statSafe(root))) {
    return { source: { ...source, status: 'missing', detail: 'no DSH session root on this machine' }, sessions: [], skipped: 0 }
  }
  const files = await walkFiles(root, { maxDepth: 3, filter: (name) => /^session\.v\d+\.jsonl\.zstd$/.test(name) })
  const all = files
    .map((file) => ({ id: basename(join(file.path, '..')), files: [file] }))
    .filter((entry) => USAGE_NATIVE_SESSION_ID.test(entry.id))
    .sort((a, b) => newestMtime(b.files) - newestMtime(a.files))
  const kept = all.slice(0, ctx.bounds.maxSessionsPerAgent)
  return { source, sessions: kept, skipped: all.length - kept.length }
}

async function zstdBinary(ctx) {
  if (ctx.state.zstd === undefined) {
    ctx.state.zstd = await resolveBinary(ctx, ['/opt/homebrew/bin/zstd', 'zstd'])
  }
  return ctx.state.zstd
}

async function scanDshEntry(ctx, entry, { keepTurns }) {
  const result = {
    project: null,
    models: new Set(),
    modelTokens: new Map(),
    tokens: emptyTokens(),
    turns: [],
    turnCount: 0,
    startedAt: null,
    lastAt: null,
    costUsd: null,
    days: new Map(),
    warnings: [],
    failed: null,
  }
  const binary = await zstdBinary(ctx)
  if (!binary) {
    result.failed = 'zstd_unavailable'
    return result
  }
  const observe = (at) => {
    if (!at) return
    if (result.startedAt === null || at < result.startedAt) result.startedAt = at
    if (result.lastAt === null || at > result.lastAt) result.lastAt = at
  }
  const onLine = (record) => {
    if (record?.type === 'session') {
      if (typeof record.cwd === 'string') result.project = record.cwd
      if (typeof record.createdAt === 'number') result.startedAt = result.startedAt ?? isoOrNull(record.createdAt)
      return
    }
    if (record?.type !== 'assistant/message') return
    const usage = record.data?.usage
    if (!usage) return
    const at = isoOrNull(record.time ?? record.data?.time)
    observe(at)
    const model = typeof record.data?.message?.source?.model === 'string' ? record.data.message.source.model : 'unknown'
    const delta = completeDelta({
      // `inputTokens` excludes cache reads here (totalTokens = input+output+cacheRead).
      input_tokens: numberOrZero(usage.inputTokens),
      cached_read_tokens: numberOrZero(usage.cacheReadTokens),
      cache_write_tokens: 0,
      output_tokens: numberOrZero(usage.outputTokens),
      reasoning_tokens: numberOrZero(usage.reasoningTokens),
    })
    addTokens(result.tokens, delta)
    addResponse(tokensFor(result.modelTokens, model), delta)
    result.models.add(model)
    result.turnCount += 1
    const day = localDay(at)
    if (day) addResponse(tokensFor(result.days, day), delta)
    if (keepTurns && result.turns.length < ctx.bounds.maxDetailTurns) {
      result.turns.push(
        turnRow(
          {
            index: result.turns.length + 1,
            native_turn_id: typeof record.data?.message?.id === 'string' ? record.data.message.id : null,
            step: typeof record.data?.step === 'number' ? record.data.step : null,
            at,
            model,
          },
          delta,
        ),
      )
    }
  }
  // Decompression goes through the injectable runner, so a test can supply a fake `zstd`
  // and the dashboard still uses the real binary. The output is bounded by the same byte
  // cap as a plain file read: a DSH session decompresses to ~1.3 MB here (largest ~30 MB),
  // so one bounded string per session is cheaper than the bookkeeping a stream needs.
  const outcome = await ctx.run(binary, ['-dc', entry.files[0].path], {
    timeoutMs: ctx.bounds.zstdTimeoutMs,
    maxBytes: keepTurns ? CAPS.dsh.detailStream : CAPS.dsh.stream,
  })
  if (outcome.stdout !== '') {
    let lines = outcome.stdout.split('\n')
    // A truncated tail is the normal way a frame ends early: keep the complete lines.
    if (outcome.truncated) lines = lines.slice(0, -1)
    for (const line of lines) {
      const text = line.trim()
      if (text === '') continue
      let record
      try {
        record = JSON.parse(text)
      } catch {
        result.warnings.push('a decompressed line was not valid JSON and was skipped')
        continue
      }
      if (onLine(record) === false) break
    }
  }
  if (!outcome.ok) {
    if (result.turnCount > 0) {
      result.warnings.push('decompression was cut short; totals cover the readable prefix')
    } else {
      result.failed = outcome.error ? 'zstd_failed' : 'zstd_timeout'
    }
  } else if (outcome.truncated && result.turnCount > 0) {
    result.warnings.push('a decompressed session exceeded the read cap; totals cover the readable prefix')
  }
  return result
}

async function scanDsh(ctx) {
  const { source, sessions, skipped } = await discoverDsh(ctx)
  if (source.status === 'missing') return { source, sessions: [], warnings: [] }
  if (!(await zstdBinary(ctx))) {
    return {
      source: { ...source, status: 'error', detail: 'the zstd binary is not available; DSH sessions stay compressed' },
      sessions: [],
      warnings: [],
    }
  }
  const warnings = []
  if (skipped > 0) warnings.push(`dsh: ${skipped} older session(s) beyond the newest ${ctx.bounds.maxSessionsPerAgent} were not read`)

  let budget = BYTE_BUDGETS.dsh
  let failed = 0
  let dropped = 0
  let partial = 0
  const rows = []
  for (const entry of sessions) {
    const size = entry.files[0].size
    if (budget <= 0) {
      dropped += 1
      continue
    }
    budget -= size
    const key = `dsh\u0000${entry.id}`
    const signature = fileSignature(entry.files)
    const cached = ctx.state.rows.get(key)
    if (cached?.signature === signature) {
      partial += cached.diagnostics?.partial ?? 0
      rows.push(cached.row)
      continue
    }
    const scanned = await scanDshEntry(ctx, entry, { keepTurns: false })
    if (scanned.failed) {
      failed += 1
      continue
    }
    if (scanned.warnings.length > 0) partial += 1
    const row = {
      agent: 'dsh',
      id: entry.id,
      files: entry.files,
      project: scanned.project,
      models: [...scanned.models],
      modelTokens: scanned.modelTokens,
      tokens: scanned.tokens,
      turnCount: scanned.turnCount,
      costUsd: null,
      startedAt: scanned.startedAt,
      lastAt: scanned.lastAt,
      days: scanned.days,
    }
    ctx.state.rows.set(key, { signature, row, diagnostics: { partial: Number(scanned.warnings.length > 0) } })
    rows.push(row)
  }
  if (failed > 0) warnings.push(`dsh: ${failed} session(s) could not be decompressed and were skipped`)
  if (partial > 0) warnings.push(`dsh: ${partial} session(s) were only partly readable; their totals cover the prefix`)
  if (dropped > 0) warnings.push(`dsh: ${dropped} session(s) skipped after the per-request byte budget was spent`)
  return { source: { ...source, sessions: rows.length }, sessions: rows, warnings }
}

async function readDshDetail(ctx, entry) {
  const scanned = await scanDshEntry(ctx, entry, { keepTurns: true })
  return {
    project: scanned.project,
    models: [...scanned.models],
    startedAt: scanned.startedAt,
    lastAt: scanned.lastAt,
    tokens: scanned.tokens,
    turns: scanned.turns,
    warnings: scanned.warnings,
  }
}

/* -------------------------------------------------------------------------- */
/* Grok — ~/.grok/sessions/<percent-encoded-cwd>/<uuid>/usage.json              */
/* -------------------------------------------------------------------------- */

/**
 * Grok writes one small `usage.json` per session: a `session{}` block with the
 * conversation totals and an additive `turns[]` array. Verified on real data: the turn
 * rows sum exactly to the session row (4,508,013 / 44,805 / 4,552,818 / 4,017,152 on a
 * sampled session), so only the session block is counted; the turns supply the series.
 *
 * The directory name is the percent-encoded working directory, which decodes losslessly —
 * the one source whose project path needs no guesswork. `updates.jsonl` (hundreds of MB of
 * transcript) lives beside it and is deliberately never opened.
 *
 * Cost: `costUsdTicks` are nano-dollars. Verified empirically — 11,081,035,200 ticks for
 * 4.5 M input / 45 k output tokens is $11.081, and 451,989,200 ticks on a smaller session
 * is $0.452; 1e-9 USD per tick is the only unit that yields those amounts.
 */
async function discoverGrok(ctx) {
  const root = ctx.roots.grok
  const source = emptySource('grok', rootLabel(root, ctx.home))
  const projects = await readdirSafe(root)
  if (!projects) {
    return { source: { ...source, status: 'missing', detail: 'no Grok session root on this machine' }, sessions: [], skipped: 0 }
  }
  const entries = []
  for (const project of projects) {
    if (!project.isDirectory() || project.isSymbolicLink()) continue
    const projectDir = join(root, project.name)
    for (const session of (await readdirSafe(projectDir)) ?? []) {
      if (!session.isDirectory() || session.isSymbolicLink()) continue
      // Only usage.json: the transcript beside it is huge and carries no counters.
      const file = await statSafe(join(projectDir, session.name, 'usage.json'))
      if (!file?.isFile()) continue
      entries.push({
        id: session.name,
        encodedProject: project.name,
        files: [{ path: join(projectDir, session.name, 'usage.json'), name: 'usage.json', size: file.size, mtimeMs: file.mtimeMs }],
      })
    }
  }
  const all = entries.filter((entry) => USAGE_NATIVE_SESSION_ID.test(entry.id)).sort((a, b) => newestMtime(b.files) - newestMtime(a.files))
  const kept = all.slice(0, ctx.bounds.maxSessionsPerAgent)
  return { source, sessions: kept, skipped: all.length - kept.length }
}

/** `%2FUsers%2Fme%2Fproj` -> `/Users/me/proj`; null when it does not decode to a path. */
function decodeGrokProject(encoded) {
  try {
    const decoded = decodeURIComponent(encoded)
    return decoded.startsWith('/') ? decoded : null
  } catch {
    return null
  }
}

function grokDelta(counters) {
  const cached = numberOrZero(counters?.cachedReadTokens)
  const cacheWrite = numberOrZero(counters?.cacheCreationTokens)
  return completeDelta({
    // Grok's `inputTokens` already includes the cached read; subtract it once.
    input_tokens: Math.max(0, numberOrZero(counters?.inputTokens) - cached),
    cached_read_tokens: cached,
    cache_write_tokens: cacheWrite,
    output_tokens: numberOrZero(counters?.outputTokens),
    reasoning_tokens: numberOrZero(counters?.reasoningTokens),
  })
}

async function scanGrokEntry(ctx, entry, { keepTurns }) {
  // `<root>/<encoded-cwd>/<uuid>/usage.json`: two directories up names the project, even
  // when this entry came from the inventory index rather than from discovery.
  const encodedProject = entry.encodedProject ?? basename(dirname(dirname(entry.files[0].path)))
  const result = {
    project: decodeGrokProject(encodedProject),
    models: new Set(),
    modelTokens: new Map(),
    tokens: emptyTokens(),
    turns: [],
    turnCount: 0,
    startedAt: null,
    lastAt: null,
    costUsd: null,
    days: new Map(),
    warnings: [],
    failed: false,
  }
  const file = entry.files[0]
  if (file.size > CAPS.grok.file) result.warnings.push('usage file is larger than expected and was read only in part')
  const text = await readSlice(file.path, 0, Math.min(file.size, CAPS.grok.file))
  if (text === null) {
    result.failed = true
    return result
  }
  let parsed
  try {
    parsed = JSON.parse(text)
  } catch {
    result.failed = true
    return result
  }
  const session = parsed?.session ?? {}
  addTokens(result.tokens, grokDelta(session))
  result.turnCount = numberOrZero(session.turnCount) || (Array.isArray(parsed?.turns) ? parsed.turns.length : 0)
  if (typeof session.primaryModelId === 'string') result.models.add(session.primaryModelId)
  // `modelUsage` carries per-model counters, which are the only per-model split Grok has.
  let attributed = false
  for (const [model, counters] of Object.entries(session.modelUsage ?? {})) {
    result.models.add(model)
    addTokens(tokensFor(result.modelTokens, model), grokDelta(counters))
    attributed = true
  }
  if (!attributed && typeof session.primaryModelId === 'string') {
    addTokens(tokensFor(result.modelTokens, session.primaryModelId), result.tokens)
  }
  if (Number.isFinite(session.costUsdTicks)) result.costUsd = session.costUsdTicks / 1e9

  const turns = Array.isArray(parsed?.turns) ? parsed.turns : []
  const ends = []
  for (const [position, turn] of turns.entries()) {
    const delta = grokDelta(turn)
    const at = isoOrNull(turn?.endedAt)
    const modelBucket = result.modelTokens.get(turn?.primaryModelId)
    if (modelBucket) modelBucket.responseCount = (modelBucket.responseCount ?? 0) + 1
    if (at) {
      ends.push(at)
      const day = localDay(at)
      if (day) addResponse(tokensFor(result.days, day), delta)
    }
    // Grok records no turn uuid: `turnNumber` and `endedAt` are all a turn has, so Core
    // correlation for this agent has to fall back to timestamps.
    if (keepTurns && result.turns.length < ctx.bounds.maxDetailTurns) {
      result.turns.push(
        turnRow(
          {
            index: typeof turn?.turnNumber === 'number' ? turn.turnNumber : position + 1,
            native_turn_id: null,
            step: null,
            at,
            model: typeof turn?.primaryModelId === 'string' ? turn.primaryModelId : null,
          },
          delta,
        ),
      )
    }
  }
  ends.sort()
  result.startedAt = ends[0] ?? null
  result.lastAt = isoOrNull(parsed?.updatedAt) ?? ends[ends.length - 1] ?? null
  return result
}

async function scanGrok(ctx) {
  const { source, sessions, skipped } = await discoverGrok(ctx)
  if (source.status === 'missing') return { source, sessions: [], warnings: [] }
  const warnings = []
  if (skipped > 0) warnings.push(`grok: ${skipped} older session(s) beyond the newest ${ctx.bounds.maxSessionsPerAgent} were not read`)

  // Sessions whose usage.json is missing (the product writes it at the end of a session)
  // are simply absent from the list; the source detail reports what was found.
  let failed = 0
  let partial = 0
  const rows = []
  for (const entry of sessions) {
    const key = `grok\u0000${entry.id}`
    const signature = fileSignature(entry.files)
    const cached = ctx.state.rows.get(key)
    if (cached?.signature === signature) {
      rows.push(cached.row)
      continue
    }
    const scanned = await scanGrokEntry(ctx, entry, { keepTurns: false })
    if (scanned.failed) {
      failed += 1
      continue
    }
    if (scanned.warnings.length > 0) partial += 1
    const row = {
      agent: 'grok',
      id: entry.id,
      files: entry.files,
      project: scanned.project,
      models: [...scanned.models],
      modelTokens: scanned.modelTokens,
      tokens: scanned.tokens,
      turnCount: scanned.turnCount,
      costUsd: scanned.costUsd,
      startedAt: scanned.startedAt,
      lastAt: scanned.lastAt,
      days: scanned.days,
    }
    ctx.state.rows.set(key, { signature, row })
    rows.push(row)
  }
  if (failed > 0) warnings.push(`grok: ${failed} usage file(s) were unreadable or truncated and were skipped`)
  if (partial > 0) warnings.push(`grok: ${partial} usage file(s) were larger than expected`)
  const detail = rows.length === 0 ? 'no usage.json written yet under the Grok session root' : undefined
  return { source: { ...source, sessions: rows.length, ...(detail ? { detail } : {}) }, sessions: rows, warnings }
}

async function readGrokDetail(ctx, entry) {
  const scanned = await scanGrokEntry(ctx, entry, { keepTurns: true })
  return {
    project: scanned.project,
    models: [...scanned.models],
    startedAt: scanned.startedAt,
    lastAt: scanned.lastAt,
    tokens: scanned.tokens,
    turns: scanned.turns,
    warnings: scanned.warnings,
  }
}

/* -------------------------------------------------------------------------- */
/* Antigravity — ~/.gemini/antigravity{,-cli,-ide}/brain/<uuid>/.system_generated/logs/transcript_full.jsonl */
/* -------------------------------------------------------------------------- */

/**
 * Antigravity keeps one `transcript_full.jsonl` per conversation under
 * `brain/<conversation-uuid>/.system_generated/logs/`. The token counters sit at the line
 * root — `input_tokens`, `cache_read_tokens`, `output_tokens`, sometimes `thinking_tokens`
 * — beside `step_index` and `created_at`.
 *
 * Verified across all 11,980 usage lines on this machine: in 11,210 of the 11,237 lines
 * that have cache reads, `input_tokens` is *smaller* than `cache_read_tokens`, which a
 * nested count could never be, so the two are disjoint here and the total adds them.
 *
 * The transcript has no model id, no cost and no cache-write counter, so the model is
 * `unknown` and those fields stay zero/null. The working directory is not in the
 * transcript either: `conversation_summaries.db` maps `conversation_id` to
 * `workspace_uris`, read with the same read-only `sqlite3` call as OpenCode. If that
 * lookup fails the project is null — a missing path is not a missing total.
 */
async function discoverAntigravity(ctx) {
  const label = rootLabel(ctx.roots.antigravity[0] ?? '', ctx.home).replace(/antigravity[^/]*/, 'antigravity*')
  const source = emptySource('antigravity', label)
  const entries = []
  for (const brain of ctx.roots.antigravity) {
    for (const conversation of (await readdirSafe(brain)) ?? []) {
      if (!conversation.isDirectory() || conversation.isSymbolicLink()) continue
      const transcript = join(brain, conversation.name, '.system_generated', 'logs', 'transcript_full.jsonl')
      const info = await statSafe(transcript)
      if (!info?.isFile()) continue
      entries.push({
        id: conversation.name,
        files: [{ path: transcript, name: 'transcript_full.jsonl', size: info.size, mtimeMs: info.mtimeMs }],
      })
    }
  }
  if (entries.length === 0) {
    return { source: { ...source, status: 'missing', detail: 'no Antigravity transcript on this machine' }, sessions: [], skipped: 0 }
  }
  const all = entries.filter((entry) => USAGE_NATIVE_SESSION_ID.test(entry.id)).sort((a, b) => newestMtime(b.files) - newestMtime(a.files))
  const kept = all.slice(0, ctx.bounds.maxSessionsPerAgent)
  return { source, sessions: kept, skipped: all.length - kept.length }
}

async function scanAntigravityEntry(ctx, entry, { keepTurns, projects }) {
  const result = {
    project: projects.get(entry.id) ?? null,
    models: new Set(),
    modelTokens: new Map(),
    tokens: emptyTokens(),
    turns: [],
    turnCount: 0,
    startedAt: null,
    lastAt: null,
    costUsd: null,
    days: new Map(),
    warnings: [],
  }
  const onLine = (record) => {
    if (typeof record?.input_tokens !== 'number') return
    const at = isoOrNull(record.created_at)
    if (at) {
      if (result.startedAt === null || at < result.startedAt) result.startedAt = at
      if (result.lastAt === null || at > result.lastAt) result.lastAt = at
    }
    const delta = completeDelta({
      // Disjoint, as verified above.
      input_tokens: numberOrZero(record.input_tokens),
      cached_read_tokens: numberOrZero(record.cache_read_tokens),
      cache_write_tokens: 0,
      output_tokens: numberOrZero(record.output_tokens),
      reasoning_tokens: numberOrZero(record.thinking_tokens),
    })
    addTokens(result.tokens, delta)
    // The transcript names no model at all; `unknown` is the contract's honest value and
    // keeps `by_model` accounted for rather than silently short.
    addResponse(tokensFor(result.modelTokens, 'unknown'), delta)
    result.models.add('unknown')
    result.turnCount += 1
    const day = localDay(at)
    if (day) addResponse(tokensFor(result.days, day), delta)
    if (keepTurns && result.turns.length < ctx.bounds.maxDetailTurns) {
      const step = typeof record.step_index === 'number' ? record.step_index : null
      result.turns.push(
        turnRow(
          {
            index: result.turns.length + 1,
            // Core names an Antigravity turn `step-<step_index>`; the transcript has no uuid
            // of its own, so that convention is the only id both sides can agree on.
            native_turn_id: step === null ? null : `step-${step}`,
            step,
            at,
            model: null,
          },
          delta,
        ),
      )
    }
  }
  const read = await forEachLine(createReadStream(entry.files[0].path), {
    maxBytes: keepTurns ? CAPS.antigravity.detailFile : CAPS.antigravity.file,
    onLine,
  })
  if (read.truncated) result.warnings.push('transcript exceeds the read cap; totals cover the readable prefix')
  return result
}

async function sqliteBinary(ctx) {
  if (ctx.state.sqlite === undefined) {
    ctx.state.sqlite = await resolveBinary(ctx, ['/usr/bin/sqlite3', 'sqlite3'])
  }
  return ctx.state.sqlite
}

/** `conversation_id -> workspace directory` from any Antigravity summaries database. */
async function readAntigravityProjects(ctx) {
  const projects = new Map()
  const binary = await sqliteBinary(ctx)
  if (!binary) return projects
  for (const database of ctx.roots.antigravitySummaries) {
    if (!(await statSafe(database))) continue
    const query =
      "select conversation_id, workspace_uris from conversation_summaries where workspace_uris is not null and workspace_uris <> ''"
    const outcome = await ctx.run(binary, ['-readonly', '-json', database, query], {
      timeoutMs: ctx.bounds.sqliteTimeoutMs,
      maxBytes: 4 * 1024 * 1024,
    })
    if (!outcome.ok || !outcome.stdout.trim()) continue
    let rows
    try {
      rows = JSON.parse(outcome.stdout)
    } catch {
      continue
    }
    for (const row of Array.isArray(rows) ? rows : []) {
      if (typeof row?.conversation_id !== 'string' || typeof row?.workspace_uris !== 'string') continue
      try {
        const uris = JSON.parse(row.workspace_uris)
        const first = Array.isArray(uris) ? uris.find((uri) => typeof uri === 'string') : null
        if (!first) continue
        const path = first.startsWith('file://') ? decodeURIComponent(first.slice('file://'.length)) : first
        if (path.startsWith('/')) projects.set(row.conversation_id, path)
      } catch {
        // An unparsable workspace_uris leaves the project unknown, not the session gone.
      }
    }
  }
  return projects
}

async function scanAntigravity(ctx) {
  const { source, sessions, skipped } = await discoverAntigravity(ctx)
  if (source.status === 'missing') return { source, sessions: [], warnings: [] }
  const warnings = []
  if (skipped > 0) warnings.push(`antigravity: ${skipped} older conversation(s) beyond the newest ${ctx.bounds.maxSessionsPerAgent} were not read`)
  const projects = await readAntigravityProjects(ctx)
  if (projects.size === 0 && (await sqliteBinary(ctx)) === null) {
    warnings.push('antigravity: sqlite3 is unavailable, so conversation working directories stay unknown')
  }

  let budget = BYTE_BUDGETS.antigravity
  let dropped = 0
  let truncated = 0
  const rows = []
  for (const entry of sessions) {
    if (budget <= 0) {
      dropped += 1
      continue
    }
    budget -= entry.files[0].size
    const key = `antigravity\u0000${entry.id}`
    // The summaries lookup is part of the row, so it belongs in the signature too.
    const signature = `${fileSignature(entry.files)}|${projects.get(entry.id) ?? ''}`
    const cached = ctx.state.rows.get(key)
    if (cached?.signature === signature) {
      truncated += cached.diagnostics?.truncated ?? 0
      rows.push(cached.row)
      continue
    }
    const scanned = await scanAntigravityEntry(ctx, entry, { keepTurns: false, projects })
    if (scanned.warnings.length > 0) truncated += 1
    const row = {
      agent: 'antigravity',
      id: entry.id,
      files: entry.files,
      project: scanned.project,
      models: [...scanned.models],
      modelTokens: scanned.modelTokens,
      tokens: scanned.tokens,
      turnCount: scanned.turnCount,
      costUsd: null,
      startedAt: scanned.startedAt,
      lastAt: scanned.lastAt,
      days: scanned.days,
    }
    ctx.state.rows.set(key, { signature, row, diagnostics: { truncated: Number(scanned.warnings.length > 0) } })
    rows.push(row)
  }
  if (dropped > 0) warnings.push(`antigravity: ${dropped} conversation(s) skipped after the per-request byte budget was spent`)
  if (truncated > 0) warnings.push(`antigravity: ${truncated} transcript(s) were only partly readable`)
  return { source: { ...source, sessions: rows.length }, sessions: rows, warnings }
}

async function readAntigravityDetail(ctx, entry) {
  const projects = await readAntigravityProjects(ctx)
  const scanned = await scanAntigravityEntry(ctx, entry, { keepTurns: true, projects })
  return {
    project: scanned.project,
    models: [...scanned.models],
    startedAt: scanned.startedAt,
    lastAt: scanned.lastAt,
    tokens: scanned.tokens,
    turns: scanned.turns,
    warnings: scanned.warnings,
  }
}

/* -------------------------------------------------------------------------- */
/* OpenCode — ~/.local/share/opencode/opencode.db (SQLite, read-only)          */
/* -------------------------------------------------------------------------- */

/**
 * OpenCode keeps a single SQLite database. `session` carries the rollup the product
 * itself displays (`cost` in USD, `tokens_input/output/reasoning/cache_read/cache_write`,
 * `model` as JSON, `directory`, `time_created/updated`), and `message` carries per
 * assistant-message usage inside its `data` JSON.
 *
 * The database is ~500 MB and owned by a running product, so it is read with the macOS
 * `sqlite3` binary in read-only mode — never `immutable=1`, which would silently ignore
 * the write-ahead log and return a stale snapshot — and only through two bounded queries:
 * one rollup and, on demand, one session's messages. Nothing here writes to it.
 *
 * The two levels of this file disagree about what nests in what, and the session columns
 * win because they are the product's own rollup:
 *
 * - a `message.data.tokens` block is inclusive (`total = input + cache.read + cache.write +
 *   output + reasoning`; a real one reads 8113 = 8093 + 0 + 0 + 10 + 10), while
 * - the `session` columns are already disjoint (`tokens_input` is uncached input).
 *
 * Verified on this machine's database: session `ses_f0f34226effeBJNf25EkssIR0x` has
 * `tokens_input` 18,944,303 and `tokens_cache_read` 916,242,320, and those equal the sums
 * of the `message` json's `tokens.input` and `tokens.cache.read` exactly. Summed over all
 * 175 sessions, `tokens_input` is 31,364,051 against 1,397,208,439 cached reads, which is
 * only possible if input excludes cache. So nothing is subtracted for this agent, and the
 * four components add up to the product's own 1,431,402,866.
 */
async function querySqliteJson(ctx, database, query) {
  const binary = await sqliteBinary(ctx)
  if (!binary) return { ok: false, detail: 'sqlite3_unavailable' }
  const outcome = await ctx.run(binary, ['-readonly', '-json', database, query], {
    timeoutMs: ctx.bounds.sqliteTimeoutMs,
    maxBytes: 16 * 1024 * 1024,
  })
  if (!outcome.ok) return { ok: false, detail: outcome.truncated ? 'sqlite_output_truncated' : 'sqlite_query_failed' }
  const text = outcome.stdout.trim()
  if (text === '') return { ok: true, rows: [] }
  try {
    return { ok: true, rows: JSON.parse(text) }
  } catch {
    return { ok: false, detail: 'sqlite_output_unparsable' }
  }
}

function opencodeDelta(row) {
  const reasoning = numberOrZero(row.tokens_reasoning)
  return completeDelta({
    input_tokens: numberOrZero(row.tokens_input),
    cached_read_tokens: numberOrZero(row.tokens_cache_read),
    cache_write_tokens: numberOrZero(row.tokens_cache_write),
    // OpenCode bills reasoning in a bucket of its own, while Claude folds thinking into
    // `output_tokens`; folding it in here keeps `output_tokens` meaning one thing across
    // agents, and makes `total_tokens` equal the sum of the product's own per-message
    // totals on this machine (1,433,688,840).
    output_tokens: numberOrZero(row.tokens_output) + reasoning,
    reasoning_tokens: reasoning,
  })
}

/** `model` is JSON (`{id, providerID}`) in new rows and a bare id in older ones. */
function opencodeModelName(raw) {
  if (typeof raw !== 'string' || raw === '') return 'unknown'
  try {
    const parsed = JSON.parse(raw)
    if (typeof parsed?.id === 'string' && parsed.id !== '') {
      return typeof parsed.providerID === 'string' && parsed.providerID !== '' ? `${parsed.providerID}/${parsed.id}` : parsed.id
    }
  } catch {
    return raw
  }
  return raw
}

async function scanOpencode(ctx) {
  const database = ctx.roots.opencode
  const source = emptySource('opencode', rootLabel(database, ctx.home))
  const info = await statSafe(database)
  if (!info) return { source: { ...source, status: 'missing', detail: 'no OpenCode database on this machine' }, sessions: [], warnings: [] }
  if (!(await sqliteBinary(ctx))) {
    return {
      source: { ...source, status: 'error', detail: 'the sqlite3 binary is not available; the OpenCode database cannot be read' },
      sessions: [],
      warnings: [],
    }
  }
  const limit = ctx.bounds.maxSessionsPerAgent
  const signature = `${database}:${info.mtimeMs}:${info.size}:${limit}`
  // Re-query after the inventory TTL: committed WAL writes need not touch the main file.

  const rollup = await querySqliteJson(
    ctx,
    database,
    `select id, directory, model, cost, tokens_input, tokens_output, tokens_reasoning,
            tokens_cache_read, tokens_cache_write, time_created, time_updated
       from session
      order by time_updated desc
      limit ${limit}`,
  )
  if (!rollup.ok) {
    const failed = {
      source: { ...source, status: 'error', detail: `the OpenCode database could not be read (${rollup.detail})` },
      sessions: [],
      warnings: [],
    }
    ctx.state.snapshots.set('opencode', { signature, scan: failed })
    return failed
  }
  const warnings = []
  // Turn counts need one grouped pass over `message` (~100 ms on this database) and are
  // the only way to know how many model calls a session had.
  const turns = new Map()
  const grouped = await querySqliteJson(
    ctx,
    database,
    "select session_id, count(*) as turns from message where json_extract(data, '$.role') = 'assistant' group by session_id",
  )
  if (grouped.ok) {
    for (const row of grouped.rows) {
      if (typeof row?.session_id === 'string') turns.set(row.session_id, numberOrZero(row.turns))
    }
  } else {
    warnings.push(`opencode: turn counts unavailable (${grouped.detail})`)
  }
  if (rollup.rows.length >= limit) warnings.push(`opencode: only the newest ${limit} session(s) were read`)

  const rows = []
  for (const row of rollup.rows) {
    if (typeof row?.id !== 'string' || !USAGE_NATIVE_SESSION_ID.test(row.id)) continue
    const model = opencodeModelName(row.model)
    const tokens = opencodeDelta(row)
    const days = new Map()
    const day = localDay(Number(row.time_updated))
    if (day) addTokens(tokensFor(days, day), tokens)
    rows.push({
      agent: 'opencode',
      id: row.id,
      files: [{ path: database, name: 'opencode.db', size: info.size, mtimeMs: info.mtimeMs }],
      project: typeof row.directory === 'string' && row.directory !== '' ? row.directory : null,
      models: [model],
      modelTokens: new Map([[model, { ...tokens }]]),
      tokens,
      turnCount: turns.get(row.id) ?? 0,
      costUsd: Number.isFinite(row.cost) ? row.cost : null,
      startedAt: isoOrNull(row.time_created),
      lastAt: isoOrNull(row.time_updated),
      days,
    })
  }
  const scan = { source: { ...source, sessions: rows.length }, sessions: rows, warnings }
  ctx.state.snapshots.set('opencode', { signature, scan })
  return scan
}

async function readOpencodeDetail(ctx, entry) {
  const binary = await sqliteBinary(ctx)
  if (!binary) {
    return { project: null, models: [], startedAt: null, lastAt: null, tokens: emptyTokens(), turns: [], warnings: ['sqlite3 is not available'] }
  }
  // `entry.id` already matched USAGE_NATIVE_SESSION_ID (letters, digits, `. _ % : -`), so it
  // cannot contain a quote or end the string literal; it is matched, never joined to a path.
  const query =
    `select id, data from message where session_id = '${entry.id}' and json_extract(data, '$.role') = 'assistant' ` +
    'order by time_created asc'
  const outcome = await querySqliteJson(ctx, ctx.roots.opencode, query)
  const turns = []
  const totals = emptyTokens()
  const models = new Set()
  const modelTokens = new Map()
  if (outcome.ok) {
    for (const row of outcome.rows) {
      let data
      try {
        data = JSON.parse(row.data)
      } catch {
        continue
      }
      const usage = data?.tokens
      if (!usage) continue
      const cached = numberOrZero(usage.cache?.read)
      const cacheWrite = numberOrZero(usage.cache?.write)
      // OpenCode input and cache counters are disjoint, matching the session rollup.
      const delta = completeDelta({
        input_tokens: numberOrZero(usage.input),
        cached_read_tokens: cached,
        cache_write_tokens: cacheWrite,
        output_tokens: numberOrZero(usage.output) + numberOrZero(usage.reasoning),
        reasoning_tokens: numberOrZero(usage.reasoning),
      })
      addTokens(totals, delta)
      const model = typeof data.modelID === 'string' ? data.modelID : 'unknown'
      models.add(model)
      addTokens(tokensFor(modelTokens, model), delta)
      if (turns.length < ctx.bounds.maxDetailTurns) {
        turns.push(
          turnRow(
            {
              index: turns.length + 1,
              native_turn_id: typeof row.id === 'string' ? row.id : null,
              step: null,
              at: isoOrNull(data?.time?.completed ?? data?.time?.created),
              model,
            },
            delta,
          ),
        )
      }
    }
  }
  return {
    project: null,
    models: [...models],
    startedAt: turns[0]?.at ?? null,
    lastAt: turns[turns.length - 1]?.at ?? null,
    tokens: totals,
    turns,
    warnings: outcome.ok ? [] : [`message rows could not be read (${outcome.detail})`],
  }
}

/* -------------------------------------------------------------------------- */
/* Aggregation                                                                 */
/* -------------------------------------------------------------------------- */

const SCANNERS = {
  claude: scanClaude,
  codex: scanCodex,
  dsh: scanDsh,
  grok: scanGrok,
  antigravity: scanAntigravity,
  opencode: scanOpencode,
}

const DETAIL_READERS = {
  claude: readClaudeDetail,
  codex: readCodexDetail,
  dsh: readDshDetail,
  grok: readGrokDetail,
  antigravity: readAntigravityDetail,
  opencode: readOpencodeDetail,
}

/** Run `task` over `items` with at most `limit` in flight, preserving order. */
async function mapWithConcurrency(items, limit, task) {
  const results = new Array(items.length)
  let cursor = 0
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const index = cursor
      cursor += 1
      if (index >= items.length) return
      results[index] = await task(items[index], index)
    }
  })
  await Promise.all(workers)
  return results
}

function totalCost(sessions) {
  let sum = 0
  let count = 0
  for (const session of sessions) {
    if (typeof session.costUsd === 'number' && Number.isFinite(session.costUsd)) {
      sum += session.costUsd
      count += 1
    }
  }
  return count > 0 ? sum : null
}

function agentRow(agent, sessions) {
  const tokens = emptyTokens()
  let turns = 0
  for (const session of sessions) {
    addTokens(tokens, session.tokens)
    turns += session.turnCount
  }
  return {
    agent,
    ...tokens,
    cost_usd: totalCost(sessions),
    sessions: sessions.length,
    turns,
    cache_hit_ratio: cacheHitRatio(tokens),
  }
}

function sessionRow(session) {
  return {
    agent: session.agent,
    native_session_id: session.id,
    project: session.project ?? null,
    models: [...session.models],
    started_at: session.startedAt ?? null,
    last_activity_at: session.lastAt ?? null,
    input_tokens: session.tokens.input_tokens,
    cached_read_tokens: session.tokens.cached_read_tokens,
    cache_write_tokens: session.tokens.cache_write_tokens,
    output_tokens: session.tokens.output_tokens,
    reasoning_tokens: session.tokens.reasoning_tokens,
    total_tokens: session.tokens.total_tokens,
    cost_usd: typeof session.costUsd === 'number' && Number.isFinite(session.costUsd) ? session.costUsd : null,
    turns: session.turnCount,
  }
}

/**
 * Fold every agent's sessions into the contract's inventory.
 *
 * `by_day` only includes days the source can actually date. A session read from a file
 * tail, or a rollup table with no per-message times, lands on its last activity day; a
 * session with no timestamp at all is left out of `by_day` rather than bucketed under a
 * fabricated day, and the invariant `by_day` sums to `totals` then only holds up to those
 * undated sessions, which the sources on this machine do not produce.
 */
function buildInventory(ctx, scans) {
  const warnings = []
  const sources = []
  const allSessions = []
  const byAgentSource = new Map()
  for (const scan of scans) {
    sources.push(scan.source)
    byAgentSource.set(scan.source.agent, scan)
    warnings.push(...scan.warnings)
    allSessions.push(...scan.sessions)
  }

  const byAgent = USAGE_AGENTS.map((agent) => agentRow(agent, byAgentSource.get(agent)?.sessions ?? []))

  const models = new Map()
  for (const session of allSessions) {
    for (const [model, tokens] of session.modelTokens ?? []) {
      const key = `${session.agent}\u0000${model}`
      const row = models.get(key) ?? { agent: session.agent, model, tokens: emptyTokens(), sessions: 0, turns: 0, costSum: 0, costCount: 0 }
      addTokens(row.tokens, tokens)
      row.sessions += 1
      row.turns += tokens.responseCount ?? (session.modelTokens.size === 1 ? session.turnCount : 0)
      // A session cost is only attributable to a model when the session used exactly one;
      // otherwise splitting it would invent an apportionment.
      if (typeof session.costUsd === 'number' && Number.isFinite(session.costUsd) && session.modelTokens?.size === 1) {
        row.costSum += session.costUsd
        row.costCount += 1
      }
      models.set(key, row)
    }
  }

  const days = new Map()
  const undated = new Set()
  for (const session of allSessions) {
    let buckets = session.days
    let covered = 0
    for (const tokens of buckets?.values() ?? []) covered += tokens.total_tokens
    if (!buckets || buckets.size === 0 || covered !== session.tokens.total_tokens) {
      // A partial read (huge Claude file, Codex tail, truncated zstd frame) has buckets
      // that cover only its readable prefix, and a snapshot fallback replaces the tokens
      // the buckets describe. Both are bucketed on the last activity day instead, so
      // `by_day` always sums to `totals`; a session with no timestamp at all is left out
      // rather than filed under a day it never touched.
      const day = localDay(session.lastAt ?? session.startedAt)
      if (!day) {
        undated.add(session.agent)
        continue
      }
      buckets = new Map([[day, { ...session.tokens, responseCount: session.turnCount }]])
    }
    for (const [day, tokens] of buckets) {
      if (!day) continue
      const row = days.get(day) ?? { day, tokens: emptyTokens(), sessions: 0, turns: 0 }
      addTokens(row.tokens, tokens)
      row.sessions += 1
      row.turns += tokens.responseCount ?? (buckets.size === 1 ? session.turnCount : 0)
      days.set(day, row)
    }
  }

  const totals = emptyTokens()
  let turns = 0
  for (const session of allSessions) {
    addTokens(totals, session.tokens)
    turns += session.turnCount
  }

  for (const agent of undated) warnings.push(`${agent}: session(s) with no timestamp are absent from by_day`)

  return {
    schema: USAGE_SCHEMA,
    generated_at: new Date(ctx.now()).toISOString(),
    totals: {
      ...totals,
      cost_usd: totalCost(allSessions),
      sessions: allSessions.length,
      turns,
      cache_hit_ratio: cacheHitRatio(totals),
    },
    by_agent: byAgent,
    by_model: [...models.values()]
      .map((row) => ({
        agent: row.agent,
        model: row.model,
        ...row.tokens,
        cost_usd: row.costCount > 0 ? row.costSum : null,
        sessions: row.sessions,
        turns: row.turns,
        cache_hit_ratio: cacheHitRatio(row.tokens),
      }))
      .sort((a, b) => b.total_tokens - a.total_tokens),
    by_day: [...days.values()]
      .map((row) => ({ day: row.day, ...row.tokens, sessions: row.sessions, turns: row.turns }))
      .sort((a, b) => (a.day < b.day ? 1 : -1)),
    sessions: allSessions
      .map(sessionRow)
      .sort((a, b) => String(b.last_activity_at ?? '').localeCompare(String(a.last_activity_at ?? ''))),
    sources,
    warnings,
  }
}

/**
 * Every source's sessions as one inventory.
 *
 * Inside the TTL the cached object is returned with a fresh `generated_at` and no I/O at
 * all. After it, every scanner re-stats its files and reuses each unchanged session row
 * (see `fileSignature`), so an idle machine costs a directory walk rather than a gigabyte
 * of JSON parsing.
 */
async function readUsageInventory(ctx) {
  if (ctx.state.inventory && ctx.now() - ctx.state.inventoryAt < ctx.bounds.cacheTtlMs) {
    // Returned verbatim, `generated_at` included: the data was produced then, and a fresh
    // timestamp on an unchanged object would claim a scan that did not happen.
    return ctx.state.inventory
  }

  // Two sources at a time: enough to overlap I/O, few enough that a zstd child and a
  // sqlite3 child never compete with four JSONL streams on the same disk.
  const scans = await mapWithConcurrency(USAGE_AGENTS, 2, async (agent) => {
    try {
      return await SCANNERS[agent](ctx)
    } catch (error) {
      // A scanner may not take the endpoint down with it.
      return {
        source: { agent, root: '', status: 'error', sessions: 0, detail: `scan failed (${error?.code ?? error?.name ?? 'unknown'})` },
        sessions: [],
        warnings: [`${agent}: scan failed and was skipped`],
      }
    }
  })

  const index = new Map()
  for (const scan of scans) {
    const perAgent = new Map()
    for (const session of scan.sessions) perAgent.set(session.id, session)
    index.set(scan.source.agent, perAgent)
  }
  const inventory = buildInventory(ctx, scans)
  ctx.state.index = index
  ctx.state.inventory = inventory
  ctx.state.inventoryAt = ctx.now()
  ctx.state.detail.clear()
  return inventory
}

/**
 * One native session's per-turn series, or `null` when this machine has no such session.
 *
 * The id is resolved through the index an inventory scan built — never joined onto a path
 * — and only the files that entry names are read. A detail read is deliberately separate
 * from the inventory: it streams whatever a single session needs (a 678 MB rollout, a
 * whole zstd frame) without making the panel wait for it.
 */
async function readUsageSession(ctx, agent, nativeSessionId) {
  if (!USAGE_AGENTS.includes(agent)) return null
  if (!USAGE_NATIVE_SESSION_ID.test(nativeSessionId)) return null
  const key = `${agent}\u0000${nativeSessionId}`
  const cached = ctx.state.detail.get(key)
  if (cached && ctx.now() - cached.at < ctx.bounds.cacheTtlMs) return cached.value
  await readUsageInventory(ctx)
  const entry = ctx.state.index.get(agent)?.get(nativeSessionId)
  if (!entry) return null
  const detail = await DETAIL_READERS[agent](ctx, entry)
  const value = {
    schema: USAGE_SCHEMA,
    agent,
    native_session_id: nativeSessionId,
    project: detail.project ?? null,
    models: detail.models ?? [],
    started_at: detail.startedAt ?? null,
    last_activity_at: detail.lastAt ?? null,
    totals: {
      ...detail.tokens,
      cost_usd: typeof entry.costUsd === 'number' && Number.isFinite(entry.costUsd) ? entry.costUsd : null,
      sessions: 1,
      turns: detail.turnCount ?? detail.turns.length,
      cache_hit_ratio: cacheHitRatio(detail.tokens),
    },
    turns: detail.turns,
    warnings: detail.warnings ?? [],
  }
  ctx.state.detail.set(key, { at: ctx.now(), value })
  return value
}

/* -------------------------------------------------------------------------- */
/* HTTP                                                                        */
/* -------------------------------------------------------------------------- */

function send(res, status, body) {
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  // Usage is read out of local logs; nothing here may be sniffed or cached.
  res.setHeader('x-content-type-options', 'nosniff')
  res.end(JSON.stringify(body))
}

/** The two paths this module answers, so a host can route them without repeat rules. */
/**
 * True for the usage surface, including anything under it: an unknown sub-path belongs to
 * this handler so it can be refused as `usage_path_not_found` instead of falling through
 * to the static host, which would answer with the dashboard shell.
 */
export function isUsagePath(pathname) {
  return pathname === '/_local/usage' || pathname.startsWith('/_local/usage/')
}

/**
 * `GET /_local/usage` and `GET /_local/usage/session`.
 *
 * Authorization has the same shape as `/_local/agents`: the Host header must name a
 * loopback address, the browser must not label the request cross-site, any `Origin` must
 * be this server, and the Bearer token is proved by proxy-reading
 * `GET /v1/projects?limit=1` from Core — no local key is added and no Core scope is
 * widened.
 *
 * The request may carry only the two documented parameters. A `root`, a `path`, a `since`
 * or anything else is refused by name rather than ignored, so a caller can never believe
 * it narrowed a scan that in fact read everything.
 */
export async function serveLocalUsage(req, res, origin, service) {
  let host = null
  try {
    host = new URL(`http://${req.headers.host}`).hostname
  } catch {
    host = null
  }
  if (
    !LOOPBACK_HOSTS.has(host) ||
    req.headers['sec-fetch-site'] === 'cross-site' ||
    (req.headers.origin && req.headers.origin !== `http://${req.headers.host}`)
  ) {
    send(res, 403, { error: 'local_origin_required' })
    return
  }
  if (req.method !== 'GET') {
    res.setHeader('allow', 'GET')
    send(res, 405, { error: 'method_not_allowed' })
    return
  }
  const authorization = req.headers.authorization
  if (!authorization?.startsWith('Bearer ')) {
    send(res, 401, { error: 'authentication_required' })
    return
  }
  let url
  try {
    // A relative base cannot be resolved into another host; the query still parses.
    url = new URL(req.url, 'http://local.invalid')
  } catch {
    send(res, 400, { error: 'invalid_request_target' })
    return
  }
  // `isUsagePath` routed this request here, which includes unknown sub-paths; only the two
  // documented paths answer, and the rest is refused rather than served as the inventory.
  const isDetail = url.pathname === '/_local/usage/session'
  if (!isDetail && url.pathname !== '/_local/usage') {
    send(res, 404, { error: 'usage_path_not_found' })
    return
  }
  const allowed = isDetail ? ['agent', 'native_session_id'] : []
  const seen = new Set()
  for (const key of url.searchParams.keys()) {
    if (!allowed.includes(key)) {
      send(res, 400, { error: 'invalid_query_parameter', detail: key })
      return
    }
    // `?agent=claude&agent=codex` would answer from the first value while the caller may
    // believe the last one won, so a repeat is refused rather than resolved by position.
    if (seen.has(key)) {
      send(res, 400, { error: 'duplicate_query_parameter', detail: key })
      return
    }
    seen.add(key)
  }
  let agent = null
  if (isDetail) {
    const requestedAgent = url.searchParams.get('agent')
    if (requestedAgent === null || !USAGE_AGENTS.includes(requestedAgent)) {
      send(res, 400, { error: 'invalid_agent' })
      return
    }
    const nativeSessionId = url.searchParams.get('native_session_id')
    if (nativeSessionId === null) {
      send(res, 400, { error: 'missing_native_session_id' })
      return
    }
    if (!USAGE_NATIVE_SESSION_ID.test(nativeSessionId)) {
      send(res, 400, { error: 'invalid_native_session_id' })
      return
    }
    agent = requestedAgent
  }

  const status = await probeCoreAuthorization(origin, authorization)
  if (status !== 200) {
    send(res, status === 401 || status === 403 ? status : 502, { error: 'core_authorization_failed' })
    return
  }

  try {
    if (isDetail) {
      const detail = await service.readUsageSession(agent, url.searchParams.get('native_session_id'))
      if (!detail) {
        send(res, 404, { error: 'usage_session_not_found' })
        return
      }
      send(res, 200, detail)
      return
    }
    send(res, 200, await service.readUsageInventory())
  } catch (error) {
    // Scans already degrade per source and per file; reaching here means the read itself
    // broke, so the client gets an error envelope instead of a half-built inventory.
    send(res, 500, { error: 'usage_unavailable', detail: error?.code ?? error?.name ?? 'unknown' })
  }
}

/**
 * Prove the caller's token against Core by reading one project list: Core enforces the
 * grant, so the dashboard never needs a local key of its own. The 3 s cap matches the
 * other local endpoints — a hung Core must not hold a panel open.
 */
function probeCoreAuthorization(origin, authorization) {
  return new Promise((resolve) => {
    let settled = false
    const finish = (status) => {
      if (settled) return
      settled = true
      resolve(status)
    }
    let target
    try {
      target = new URL('/v1/projects?limit=1', origin)
    } catch {
      finish(502)
      return
    }
    let request
    try {
      request = http.request(
        { hostname: target.hostname, port: target.port || 80, path: `${target.pathname}${target.search}`, method: 'GET', headers: { authorization } },
        (reply) => {
          reply.resume()
          finish(reply.statusCode ?? 502)
        },
      )
    } catch {
      finish(502)
      return
    }
    request.setTimeout(3000, () => request.destroy())
    request.on('error', () => finish(502))
    request.end()
  })
}
