# Conversation token usage (dashboard-local analytics)

Status: implementable from shipped Agent session files on this machine. Core itself
stores no billing metadata, so this feature is deliberately **outside** the frozen
Core API surface: a dashboard-local read endpoint parses the Agents' own session
files and returns an inventory the UI renders. Nothing is written back, no Agent is
executed, and no prompt text is ever part of the payload.

## Why not Core (or the capture path)

- `core.db` (schema 17) has no token, model or cost column, and `events.payload_json`
  records only the conversation text and native identifiers.
- The capture adapters (`src/jasmine_core/capture/*_session.py`) forward text only
  (`MAX_TEXT = 512 * 1024`). Adding usage there would change a released Python
  service and require re-installing every host hook — and it would still leave
  already-recorded conversations with no numbers.
- Parsing the session files locally is read-only, backfills history, and fails in a
  way the panel can show honestly (`sources[].status`).

## Endpoints

| Path | Returns |
| --- | --- |
| `GET /_local/usage` | `UsageInventory`: totals, `by_agent`, `by_model`, `by_day`, `sessions[]`, `sources[]`, `warnings[]` |
| `GET /_local/usage/session?agent=<agent>&native_session_id=<id>` | `UsageSessionDetail`: one session's per-turn series |

Both are available only on a loopback bind, require the same-origin checks and Bearer
token used by `/_local/agents`, and validate `agent` against a fixed enum plus
`native_session_id` against an anchored regex. A filesystem path is never accepted
from the request: the server decides which roots it may read.

## Sources (verified on this machine, 2026-11-12)

| Agent | Root | Usage record | Model | USD | Core join |
| --- | --- | --- | --- | --- | --- |
| claude | `~/.claude/projects/<encoded-cwd>/<session>.jsonl` | `type:"assistant".message.usage` (input, cache_creation, cache_read, output, output_tokens_details.thinking_tokens) | `message.model` (per message) | session `cost-state.totalCostUSD` | `promptId` via `parentUuid` walk |
| codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `token_usage_record.payload.usage` (per response); `thread_token_usage` is cumulative | `turn_context.payload.model` | none | `payload.turn_id` |
| dsh | `~/.dsh/sessions/--…--/session-<uuid>/session.v{3,4}.jsonl.zstd` | `assistant/message.data.usage` | `data.message.source.model` | none | `data.message.id` |
| grok | `~/.grok/sessions/<%2F-encoded-cwd>/<uuid>/usage.json` | session totals + `turns[]` | `primaryModelId` / `modelUsage` | `costUsdTicks / 1e9` | none (time only) |
| antigravity | `~/.gemini/antigravity*/brain/<uuid>/.system_generated/logs/transcript_full.jsonl` | line-root `input_tokens`, `cache_read_tokens`, `output_tokens` | absent | none | `step-<step_index>` |
| opencode | `~/.local/share/opencode/opencode.db` (read-only) | `session.tokens_*` + `message.data.tokens` | `model` / `modelID` | `cost` (USD) | none |

Measured scale: claude 93 files / 1.56 GB, codex 71 files / 3.61 GB (+11 archived),
dsh 105 sessions / 81 MB compressed, grok 30 `usage.json` / 0.1 MB, antigravity 221
transcripts / 50 MB, opencode 175 sessions / 527 MB DB.

### Token conventions differ per source (do not assume one rule)

| Agent | `total_tokens` rule | not provided |
| --- | --- | --- |
| claude | input + cache_creation + cache_read + output | single total field |
| codex | the source's own total (cached ⊂ input) | cost |
| dsh | provided `totalTokens` (= input + output + cacheRead) | cache write |
| grok | provided `totalTokens` | — |
| antigravity | input + cache_read + output | cache write, reasoning, model, cost |
| opencode | provided `tokens.total` | — |

The five component counters are stored separately and `total_tokens` follows each
source's own rule, so a cross-agent sum is a "served tokens" figure, not a
billing-equivalent one. `cost_usd` is `null` for codex, dsh and antigravity, and any
total cost is therefore partial (3 of 6 sources) — the panel must say so rather than
summarise it as complete.

## Bounds and failure modes

- Inventory scans only whole small files and tail/stream reads where the format
  allows it (Codex tail-reads `thread_token_usage`; Claude and Antigravity stream with
  a substring pre-filter; DSH needs `zstd -dc` and is capped to one decompression at a
  time). Per-turn detail is a separate on-demand read for one session.
- A root that is absent, unreadable, or unparseable becomes `sources[].status` =
  `missing`/`error` with a `detail` string; a file that is mid-write or truncated is
  skipped with a `warnings[]` entry. Neither is ever rendered as "0 tokens".
- `*.lock` siblings are not data, `updates.jsonl`/`chat_history.jsonl` blobs and the
  1.2 GB Antigravity protobuf DBs are never read.
- The response is cached by size+mtime with a short TTL so an open panel does not
  rescan gigabytes per request.

## UI

- New route `#/usage` ("Token 消耗"): totals, per-Agent / per-model / per-day
  breakdown, a sortable session table with drill-down into one session's per-turn
  series, and an explicit "数据来源与缺口" panel.
- Conversation page: a compact 本会话 token panel plus per-turn badges on assistant
  messages, matched by native turn id (Claude `promptId`, Codex `turn_id`, DSH message
  id, Antigravity `step-N`) and by ordinal only where the source has no id (Grok);
  with no data the panel and badges are absent rather than zero-valued.

## Privacy

The payload carries counts, model ids, timestamps, session ids and the working
directory each Agent ran in — no prompt text, no environment variables, no keys, no
paths outside the declared roots.
