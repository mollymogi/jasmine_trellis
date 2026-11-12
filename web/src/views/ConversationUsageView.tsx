/**
 * Token 消耗 (conversation usage, `/_local/usage`).
 *
 * This page answers one question — where did past conversations spend tokens? — from the
 * Agent session files the dashboard host reads. It is a *local* read, not Core accounting:
 * Core stores what an Agent said and no billing metadata, so a source the host could not
 * read is a gap in the data, not a zero.
 *
 * The invariants this view protects:
 *
 * - A missing/unreadable source is shown as a gap that names the root and the reason. When
 *   the inventory holds no usage at all we render an explanatory alert instead of a wall of
 *   zero tiles, because "nothing was found" and "nothing was spent" are different claims.
 * - Cost is known only for the sources that report it (Claude Code, Grok, OpenCode). A null
 *   cost is `—`, never `0`, and the total is labelled 已知成本 rather than presented as the
 *   full spend.
 * - Model and project can legitimately be absent (Antigravity reports no model id); those
 *   cells say 未知模型 / 未知项目 instead of dropping the row.
 * - Numbers are compacted (1.2k / 3.4M) for scanning; the exact value is in the cell's tooltip.
 * - All session rows are already in memory, so paging here is client-side and says so; the
 *   shared `Pager` is deliberately not used because its wording describes Core page counts.
 */

import { Fragment, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, RefreshCw, Search } from 'lucide-react'

import { CoreApiError, kindForStatus } from '../api/errors'
import {
  USAGE_AGENTS,
  USAGE_AGENT_LABELS,
  fetchUsageInventory,
  fetchUsageSession,
} from '../api/usage'
import type {
  UsageAgent,
  UsageAgentRow,
  UsageDayRow,
  UsageInventory,
  UsageModelRow,
  UsageSessionDetail,
  UsageSessionRow,
  UsageSourceStatus,
  UsageTokens,
  UsageTurn,
} from '../api/usage'
import { useResource } from '../app/useResource'
import { useSession } from '../app/session'
import { ErrorPanel, Loading, Panel } from '../components/States'
import { TableScroll } from '../components/TableScroll'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatTimestamp } from '../lib/text'

type SortKey = 'total' | 'input' | 'output' | 'turns' | 'started'
type AgentFilter = UsageAgent | 'all'

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'total', label: '总 token（降序）' },
  { value: 'input', label: '输入 token（降序）' },
  { value: 'output', label: '输出 token（降序）' },
  { value: 'turns', label: '轮次（降序）' },
  { value: 'started', label: '开始时间（最新优先）' },
]

const PAGE_SIZES = [25, 50, 100] as const
const DEFAULT_PAGE_SIZE = 25
const SESSION_COLUMNS = 9

/** 1.2k / 3.4M; exact values stay available in the cell title. */
function formatTokens(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '—'
  // A machine that has been in use for months crosses a billion tokens; "14377.5M" is
  // harder to read than "14.4B", and the digits stop being meaningful at that scale.
  if (value >= 1_000_000_000) return compactScaled(value / 1_000_000_000, 'B')
  if (value >= 1_000_000) return compactScaled(value / 1_000_000, 'M')
  if (value >= 1_000) return compactScaled(value / 1_000, 'k')
  return String(Math.round(value))
}

function compactScaled(value: number, suffix: string): string {
  const rounded = Math.round(value * 10) / 10
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
  return `${text}${suffix}`
}

/**
 * Money keeps 2–4 decimals: two for whole dollars, up to four for sub-dollar amounts.
 * `null` is *unknown*, never zero — only some sources report cost at all.
 */
function formatCost(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  if (value === 0) return '$0.00'
  if (value >= 1) return `$${value.toFixed(2)}`
  const [whole = '0', fraction = ''] = Math.abs(value).toFixed(4).split('.')
  const trimmed = fraction.replace(/0+$/, '')
  const kept = trimmed.length < 2 ? `${trimmed}00`.slice(0, 2) : trimmed
  const sign = value < 0 ? '-' : ''
  return `${sign}$${whole}.${kept}`
}

/** The contract calls this a 0..1 ratio and `null` when nothing was billable. */
function formatRatio(ratio: number | null): string {
  if (ratio === null || !Number.isFinite(ratio)) return '—'
  return `${(ratio * 100).toFixed(1)}%`
}

function cacheTokens(row: UsageTokens): number {
  return row.cached_read_tokens + row.cache_write_tokens
}

function sessionKey(row: UsageSessionRow): string {
  return `${row.agent}:${row.native_session_id}`
}

function startedValue(value: string | null): number {
  if (value === null) return Number.NEGATIVE_INFINITY
  const parsed = Date.parse(value)
  // Timings are already server-normalized ISO strings; an unparsable one sorts last rather
  // than throwing. The cell still renders the raw value via `formatTimestamp`.
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed
}

function compareSessions(a: UsageSessionRow, b: UsageSessionRow, sort: SortKey): number {
  switch (sort) {
    case 'total':
      return b.total_tokens - a.total_tokens
    case 'input':
      return b.input_tokens - a.input_tokens
    case 'output':
      return b.output_tokens - a.output_tokens
    case 'turns':
      return b.turns - a.turns
    case 'started':
      return startedValue(b.started_at) - startedValue(a.started_at)
  }
}

/**
 * Map the usage module's plain `Error` (and any transport failure) onto the shared error
 * model, so `ErrorPanel` can name the HTTP status and the view never shows an empty list for
 * a failed read. `fetchUsageInventory`/`fetchUsageSession` deliberately throw plain errors.
 */
function toUsageError(error: unknown): CoreApiError {
  if (error instanceof CoreApiError) return error
  const message = error instanceof Error ? error.message : String(error)
  const match = /^usage_unavailable:(\d{3})$/.exec(message)
  if (match !== null) {
    const status = Number(match[1])
    const detail =
      typeof (error as { detail?: unknown }).detail === 'string'
        ? ((error as { detail: string }).detail)
        : message
    return new CoreApiError({
      kind: kindForStatus(status),
      status,
      code: 'usage_unavailable',
      message: detail,
    })
  }
  return new CoreApiError({
    kind: 'degraded',
    status: null,
    code: 'usage_unavailable',
    message: `本机用量接口不可用:${message}`,
  })
}

function sourceStatusText(source: UsageSourceStatus): string {
  if (source.status === 'ok') return `正常 · 读到 ${source.sessions} 个会话`
  if (source.status === 'missing') {
    return `缺失 · 未找到 ${source.root}${source.detail ? `（${source.detail}）` : ''}`
  }
  return `读取失败 · ${source.detail ?? '接口未给出原因'}`
}

export function ConversationUsageView(): JSX.Element {
  const { token } = useSession()
  const [agentFilter, setAgentFilter] = useState<AgentFilter>('all')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('total')
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE)
  const [page, setPage] = useState(1)
  const [openSession, setOpenSession] = useState<{ agent: UsageAgent; id: string } | null>(null)

  const inventory = useResource<UsageInventory>(
    (signal) =>
      fetchUsageInventory({ signal, token: token ?? undefined }).catch((error: unknown) => {
        throw toUsageError(error)
      }),
    [token],
  )

  const data = inventory.data
  const presentAgents = useMemo(() => {
    if (data === null) return [] as UsageAgent[]
    return USAGE_AGENTS.filter((agent) => data.sessions.some((row) => row.agent === agent))
  }, [data])

  const filtered = useMemo(() => {
    if (data === null) return [] as UsageSessionRow[]
    const needle = search.trim().toLocaleLowerCase()
    const rows = data.sessions.filter((row) => {
      if (agentFilter !== 'all' && row.agent !== agentFilter) return false
      if (needle === '') return true
      const project = row.project ?? '未知项目'
      return `${project} ${row.models.join(' ')}`.toLocaleLowerCase().includes(needle)
    })
    return [...rows].sort((a, b) => compareSessions(a, b, sort))
  }, [data, agentFilter, search, sort])

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const pageRows = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize)

  if (inventory.status === 'loading' && data === null) {
    return (
      <div className="stack usage-page">
        <Panel title="Token 消耗">
          <Loading what="Token 用量" />
        </Panel>
      </div>
    )
  }

  if (inventory.error) {
    return (
      <div className="stack usage-page">
        <Panel title="Token 消耗">
          <ErrorPanel error={inventory.error} what="Token 消耗" onRetry={inventory.reload} />
          <p className="usage-note">
            这一页读取的是本机 Agent 会话文件。读取失败时不显示 0，也不把它当作“没有消耗”。
          </p>
        </Panel>
      </div>
    )
  }

  if (data === null) {
    return (
      <div className="stack usage-page">
        <Panel title="Token 消耗">
          <Loading what="Token 用量" />
        </Panel>
      </div>
    )
  }

  const totals = data.totals
  const hasUsage = totals.total_tokens > 0 || totals.sessions > 0 || totals.turns > 0
  const openKey = openSession === null ? null : `${openSession.agent}:${openSession.id}`
  const toggleSession = (row: UsageSessionRow): void => {
    setOpenSession((previous) =>
      previous !== null && previous.agent === row.agent && previous.id === row.native_session_id
        ? null
        : { agent: row.agent, id: row.native_session_id },
    )
  }

  return (
    <div className="stack usage-page">
      <Panel
        title={hasUsage ? '总览' : '总览 · 没有可用记录'}
        actions={
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={inventory.reload}
            disabled={inventory.status === 'loading'}
          >
            <RefreshCw size={14} />
            刷新
          </Button>
        }
      >
        {hasUsage ? (
          <>
            <dl className="usage-tiles">
              <UsageTile testKey="total" label="总 token" value={formatTokens(totals.total_tokens)} />
              <UsageTile testKey="input" label="输入" value={formatTokens(totals.input_tokens)} />
              <UsageTile testKey="output" label="输出" value={formatTokens(totals.output_tokens)} />
              <UsageTile
                testKey="cached-read"
                label="缓存读"
                value={formatTokens(totals.cached_read_tokens)}
              />
              <UsageTile
                testKey="cache-write"
                label="缓存写"
                value={formatTokens(totals.cache_write_tokens)}
              />
              <UsageTile
                testKey="reasoning"
                label="推理"
                value={formatTokens(totals.reasoning_tokens)}
              />
              <UsageTile
                testKey="cost"
                label="已知成本"
                value={formatCost(totals.cost_usd)}
                title="只有部分来源报告成本，见下方说明"
              />
              <UsageTile testKey="sessions" label="会话数" value={String(totals.sessions)} />
              <UsageTile testKey="turns" label="轮次" value={String(totals.turns)} />
              <UsageTile
                testKey="cache-hit"
                label="缓存命中率"
                value={formatRatio(totals.cache_hit_ratio)}
              />
            </dl>
            <p className="usage-note">
              成本是<strong>已知成本</strong>：只有 Claude Code、Grok、OpenCode 报告成本；
              Codex、DSH、Antigravity 的成本未知（显示为 “—”），因此这个合计不是全部消耗。
            </p>
            <p className="usage-note">
              用量来自本机 Agent 会话文件，不是 Core 记账。缓存命中率按
              <code>cache_hit_ratio</code>（0–1）换算；没有可计费读写时为 “—”。
            </p>
          </>
        ) : (
          <div className="state state--error" role="alert" data-testid="usage-zero">
            <p className="state__headline">没有读到任何 token 记录 — 这不等于“消耗为 0”。</p>
            <p>
              token 用量只来自本机的 Agent 会话文件。下面的“数据来源与缺口”逐个列出了每台
              Agent 的根目录、读取状态和会话数；缺失或读取失败就是这里没有数据的原因。
            </p>
            <p className="state__meta">
              常见原因：该 Agent 从未在这台机器上运行，或它的会话目录不在默认位置。如果来源
              全部显示正常却仍为 0，那才是真的没有可统计的会话。
            </p>
          </div>
        )}
      </Panel>

      {hasUsage ? (
        <>
          <div className="usage-grid">
            <Panel title="按 Agent 分布">
              {data.by_agent.length === 0 ? (
                <p className="state state--meta">接口没有返回按 Agent 的分布。</p>
              ) : (
                <TableScroll label="按 Agent 的 token 分布">
                  <Table aria-label="按 Agent 的 token 分布">
                    <TableHeader>
                      <TableRow>
                        <TableHead scope="col">Agent</TableHead>
                        <TableHead scope="col" className="usage-num">
                          会话
                        </TableHead>
                        <TableHead scope="col" className="usage-num">
                          轮次
                        </TableHead>
                        <TableHead scope="col" className="usage-num">
                          输入
                        </TableHead>
                        <TableHead scope="col" className="usage-num">
                          输出
                        </TableHead>
                        <TableHead scope="col" className="usage-num">
                          总 token
                        </TableHead>
                        <TableHead scope="col" className="usage-num">
                          成本
                        </TableHead>
                        <TableHead scope="col">占比</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.by_agent.map((row) => (
                        <AgentRow key={row.agent} row={row} total={totals.total_tokens} />
                      ))}
                    </TableBody>
                  </Table>
                </TableScroll>
              )}
            </Panel>

            <Panel title="按模型分布">
              {data.by_model.length === 0 ? (
                <p className="state state--meta">接口没有返回按模型的分布。</p>
              ) : (
                <TableScroll label="按模型的 token 分布">
                  <Table aria-label="按模型的 token 分布">
                    <TableHeader>
                      <TableRow>
                        <TableHead scope="col">模型</TableHead>
                        <TableHead scope="col" className="usage-num">
                          会话
                        </TableHead>
                        <TableHead scope="col" className="usage-num">
                          轮次
                        </TableHead>
                        <TableHead scope="col" className="usage-num">
                          总 token
                        </TableHead>
                        <TableHead scope="col">占比</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sortedModels(data.by_model).map((row) => (
                        <ModelRow
                          key={`${row.agent}:${row.model}`}
                          row={row}
                          total={totals.total_tokens}
                        />
                      ))}
                    </TableBody>
                  </Table>
                </TableScroll>
              )}
              <p className="usage-note">
                一个会话可能混用多个模型；这里的每一行来自该来源自己的逐轮/逐消息记录。
                模型为空（例如 Antigravity 不报告模型 ID）时显示为 未知模型。
              </p>
            </Panel>
          </div>

          <Panel title="按天趋势">
            {data.by_day.length === 0 ? (
              <p className="state state--meta">接口没有返回按天的用量。</p>
            ) : (
              <DayTrend days={data.by_day} />
            )}
          </Panel>

          <Panel title={`会话列表（匹配 ${filtered.length} / ${data.sessions.length}）`}>
            <div className="usage-toolbar">
              <label>
                Agent 筛选
                <select
                  aria-label="Agent 筛选"
                  value={agentFilter}
                  onChange={(event) => {
                    setAgentFilter(event.target.value as AgentFilter)
                    setPage(1)
                  }}
                >
                  <option value="all">全部 Agent</option>
                  {presentAgents.map((agent) => (
                    <option key={agent} value={agent}>
                      {USAGE_AGENT_LABELS[agent]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                排序
                <select
                  aria-label="会话排序"
                  value={sort}
                  onChange={(event) => {
                    setSort(event.target.value as SortKey)
                    setPage(1)
                  }}
                >
                  {SORT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="usage-search">
                <Search size={14} aria-hidden="true" />
                搜索
                <input
                  type="search"
                  aria-label="搜索项目或模型"
                  placeholder="项目或模型"
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value)
                    setPage(1)
                  }}
                />
              </label>
            </div>

            {filtered.length === 0 ? (
              <p className="state state--meta">
                当前筛选条件下没有会话。清除搜索或把 Agent 切回“全部 Agent”即可看到其余来源。
              </p>
            ) : (
              <TableScroll label="会话 token 用量列表">
                <Table aria-label="会话 token 用量列表">
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">Agent</TableHead>
                      <TableHead scope="col">项目</TableHead>
                      <TableHead scope="col">开始时间</TableHead>
                      <TableHead scope="col" className="usage-num">
                        轮次
                      </TableHead>
                      <TableHead scope="col" className="usage-num">
                        输入
                      </TableHead>
                      <TableHead scope="col" className="usage-num">
                        输出
                      </TableHead>
                      <TableHead scope="col" className="usage-num">
                        缓存
                      </TableHead>
                      <TableHead scope="col" className="usage-num">
                        总 token
                      </TableHead>
                      <TableHead scope="col" className="usage-num">
                        成本
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pageRows.map((row) => {
                      const key = sessionKey(row)
                      const isOpen = openKey === key
                      return (
                        <Fragment key={key}>
                          <TableRow
                            className="usage-session-row"
                            data-testid="usage-session-row"
                            data-agent={row.agent}
                            data-session-id={row.native_session_id}
                            data-expanded={isOpen ? 'true' : 'false'}
                            onClick={() => toggleSession(row)}
                          >
                            <TableCell>
                              <button
                                type="button"
                                className="usage-session-toggle"
                                aria-expanded={isOpen}
                                aria-label={`${isOpen ? '收起' : '展开'}会话 ${row.native_session_id} 的逐轮明细`}
                                onClick={(event) => {
                                  event.stopPropagation()
                                  toggleSession(row)
                                }}
                              >
                                {isOpen ? (
                                  <ChevronDown size={14} aria-hidden="true" />
                                ) : (
                                  <ChevronRight size={14} aria-hidden="true" />
                                )}
                              </button>
                              {USAGE_AGENT_LABELS[row.agent]}
                            </TableCell>
                            <TableCell>
                              <span data-testid="usage-project">
                                {row.project ?? <span className="usage-unknown">未知项目</span>}
                              </span>
                              <div className="usage-models">
                                {row.models.length > 0 ? row.models.join(' · ') : '未知模型'}
                              </div>
                            </TableCell>
                            <TableCell>
                              <time dateTime={row.started_at ?? undefined}>
                                {formatTimestamp(row.started_at)}
                              </time>
                            </TableCell>
                            <TableCell className="usage-num">{row.turns}</TableCell>
                            <TableCell className="usage-num" title={String(row.input_tokens)}>
                              {formatTokens(row.input_tokens)}
                            </TableCell>
                            <TableCell className="usage-num" title={String(row.output_tokens)}>
                              {formatTokens(row.output_tokens)}
                            </TableCell>
                            <TableCell
                              className="usage-num"
                              title={`缓存读 ${row.cached_read_tokens} · 缓存写 ${row.cache_write_tokens}`}
                            >
                              {formatTokens(cacheTokens(row))}
                            </TableCell>
                            <TableCell className="usage-num" title={String(row.total_tokens)}>
                              {formatTokens(row.total_tokens)}
                            </TableCell>
                            <TableCell className="usage-num">{formatCost(row.cost_usd)}</TableCell>
                          </TableRow>
                          {isOpen ? (
                            <TableRow className="usage-detail-row">
                              <TableCell colSpan={SESSION_COLUMNS}>
                                <SessionTurns
                                  agent={row.agent}
                                  nativeSessionId={row.native_session_id}
                                />
                              </TableCell>
                            </TableRow>
                          ) : null}
                        </Fragment>
                      )
                    })}
                  </TableBody>
                </Table>
              </TableScroll>
            )}

            <div className="usage-pager">
              <span>
                第 {currentPage} / {pageCount} 页 · 共 {filtered.length} 个会话（本地分页；
                已从本机读取全部 {data.sessions.length} 个，这不是 Core 的分页语义）
              </span>
              <span className="usage-pager__controls">
                <label>
                  每页
                  <select
                    aria-label="每页会话数"
                    value={pageSize}
                    onChange={(event) => {
                      setPageSize(Number(event.target.value))
                      setPage(1)
                    }}
                  >
                    {PAGE_SIZES.map((size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={currentPage <= 1}
                  onClick={() => setPage(currentPage - 1)}
                >
                  上一页
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={currentPage >= pageCount}
                  onClick={() => setPage(currentPage + 1)}
                >
                  下一页
                </Button>
              </span>
            </div>
          </Panel>
        </>
      ) : null}

      <Panel title="数据来源与缺口">
        <p className="usage-note">
          统计生成时间 {formatTimestamp(data.generated_at)}；浏览器读取时间{' '}
          {formatTimestamp(inventory.loadedAt)}。探针只读取本机会话目录，不接受浏览器传入的
          路径，也不运行 Agent。
        </p>
        {data.sources.length === 0 ? (
          <div className="state state--error" role="alert">
            <p className="state__headline">接口没有报告任何数据来源。</p>
            <p>没有来源清单时，无法判断“没有消耗”还是“没有读到”；本页不会把它当作 0。</p>
          </div>
        ) : (
          <TableScroll label="用量数据来源状态">
            <Table aria-label="用量数据来源状态">
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">来源</TableHead>
                  <TableHead scope="col">根目录</TableHead>
                  <TableHead scope="col">状态</TableHead>
                  <TableHead scope="col" className="usage-num">
                    会话
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.sources.map((source) => (
                  <TableRow
                    key={`${source.agent}:${source.root}`}
                    data-testid="usage-source-row"
                    data-status={source.status}
                  >
                    <TableCell>{USAGE_AGENT_LABELS[source.agent]}</TableCell>
                    <TableCell>
                      <code>{source.root}</code>
                    </TableCell>
                    <TableCell
                      className={
                        source.status === 'ok' ? 'usage-source-ok' : 'usage-source-gap'
                      }
                    >
                      {sourceStatusText(source)}
                    </TableCell>
                    <TableCell className="usage-num">{source.sessions}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        )}
        {data.warnings.length > 0 ? (
          <>
            <p className="usage-note">接口报告了这些 warnings：</p>
            <ul className="usage-warnings">
              {data.warnings.map((warning, index) => (
                <li key={`${index}:${warning}`}>{warning}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="state state--meta">接口没有报告 warnings；这不代表读取完整，来源状态以上表为准。</p>
        )}
      </Panel>
    </div>
  )
}

function UsageTile({
  testKey,
  label,
  value,
  title,
}: {
  testKey: string
  label: string
  value: string
  title?: string
}): JSX.Element {
  return (
    <div className="usage-tile">
      <dt>{label}</dt>
      <dd data-testid={`usage-tile-${testKey}`} title={title}>
        {value}
      </dd>
    </div>
  )
}

function sortedModels(rows: UsageModelRow[]): UsageModelRow[] {
  return [...rows].sort((a, b) => b.total_tokens - a.total_tokens)
}

function AgentRow({ row, total }: { row: UsageAgentRow; total: number }): JSX.Element {
  return (
    <TableRow>
      <TableCell>{USAGE_AGENT_LABELS[row.agent]}</TableCell>
      <TableCell className="usage-num">{row.sessions}</TableCell>
      <TableCell className="usage-num">{row.turns}</TableCell>
      <TableCell className="usage-num" title={String(row.input_tokens)}>
        {formatTokens(row.input_tokens)}
      </TableCell>
      <TableCell className="usage-num" title={String(row.output_tokens)}>
        {formatTokens(row.output_tokens)}
      </TableCell>
      <TableCell className="usage-num" title={String(row.total_tokens)}>
        {formatTokens(row.total_tokens)}
      </TableCell>
      <TableCell
        className="usage-num"
        title={row.cost_usd === null ? '该来源不报告成本' : String(row.cost_usd)}
      >
        {formatCost(row.cost_usd)}
      </TableCell>
      <TableCell>
        <Share value={row.total_tokens} total={total} />
      </TableCell>
    </TableRow>
  )
}

function ModelRow({ row, total }: { row: UsageModelRow; total: number }): JSX.Element {
  const model = row.model.trim() === '' ? '未知模型' : row.model
  return (
    <TableRow>
      <TableCell>
        {model}
        <div className="usage-models">{USAGE_AGENT_LABELS[row.agent]}</div>
      </TableCell>
      <TableCell className="usage-num">{row.sessions}</TableCell>
      <TableCell className="usage-num">{row.turns}</TableCell>
      <TableCell className="usage-num" title={String(row.total_tokens)}>
        {formatTokens(row.total_tokens)}
      </TableCell>
      <TableCell>
        <Share value={row.total_tokens} total={total} />
      </TableCell>
    </TableRow>
  )
}

/** A bar plus the same figure as text; the bar is decoration for the number. */
function Share({ value, total }: { value: number; total: number }): JSX.Element {
  const ratio = total > 0 ? Math.min(1, Math.max(0, value / total)) : 0
  return (
    <span className="usage-share">
      <span className="usage-share__track" aria-hidden="true">
        <span className="usage-share__fill" style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
      </span>
      <span className="usage-share__value">{formatRatio(ratio)}</span>
    </span>
  )
}

function DayTrend({ days }: { days: UsageDayRow[] }): JSX.Element {
  const rows = [...days].sort((a, b) => a.day.localeCompare(b.day))
  const max = Math.max(1, ...rows.map((row) => row.total_tokens))
  return (
    <div className="usage-trend" role="list" aria-label="按天 token 趋势">
      {rows.map((row) => {
        const height = Math.max(2, Math.round((row.total_tokens / max) * 100))
        const label = `${row.day}: 总 ${formatTokens(row.total_tokens)} token · ${row.sessions} 会话 · ${row.turns} 轮`
        return (
          <div className="usage-trend__col" role="listitem" key={row.day}>
            <span
              className="usage-trend__barwrap"
              role="img"
              aria-label={label}
              title={label}
            >
              <span className="usage-trend__bar" style={{ height: `${height}%` }} />
            </span>
            <span className="usage-trend__label" aria-hidden="true">
              {row.day.slice(5)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/**
 * One expanded session. It owns its own read so a slow/failed detail never blanks the list,
 * and switching rows aborts the previous request (`useResource` drops stale answers).
 */
function SessionTurns({
  agent,
  nativeSessionId,
}: {
  agent: UsageAgent
  nativeSessionId: string
}): JSX.Element {
  const { token } = useSession()
  const detail = useResource<UsageSessionDetail>(
    (signal) =>
      fetchUsageSession(
        { agent, nativeSessionId },
        { signal, token: token ?? undefined },
      ).catch((error: unknown) => {
        throw toUsageError(error)
      }),
    [agent, nativeSessionId, token],
  )

  return (
    <div className="usage-detail" data-testid="usage-session-detail">
      <p className="usage-detail__head">
        逐轮明细 · {USAGE_AGENT_LABELS[agent]} · <code>{nativeSessionId}</code>
      </p>
      {detail.status === 'loading' && detail.data === null ? (
        <Loading what="该会话的逐轮明细" />
      ) : null}
      {detail.error ? (
        <ErrorPanel error={detail.error} what="该会话的逐轮明细" onRetry={detail.reload} />
      ) : null}
      {detail.data ? <SessionTurnBody detail={detail.data} /> : null}
    </div>
  )
}

function SessionTurnBody({ detail }: { detail: UsageSessionDetail }): JSX.Element {
  const turns = [...detail.turns].sort((a, b) => a.index - b.index)
  const max = Math.max(1, ...turns.map((turn) => turn.total_tokens))
  return (
    <>
      <dl className="usage-detail__meta">
        <div>
          <dt>项目</dt>
          <dd>{detail.project ?? '未知项目'}</dd>
        </div>
        <div>
          <dt>模型</dt>
          <dd>{detail.models.length > 0 ? detail.models.join(' · ') : '未知模型'}</dd>
        </div>
        <div>
          <dt>开始</dt>
          <dd>{formatTimestamp(detail.started_at)}</dd>
        </div>
        <div>
          <dt>最后活动</dt>
          <dd>{formatTimestamp(detail.last_activity_at)}</dd>
        </div>
        <div>
          <dt>合计</dt>
          <dd>
            {formatTokens(detail.totals.total_tokens)} token · {detail.totals.turns} 轮 · 已知成本{' '}
            {formatCost(detail.totals.cost_usd)}
          </dd>
        </div>
      </dl>
      {detail.warnings.length > 0 ? (
        <div className="state state--meta" role="note">
          <p className="state__headline">该会话的读取说明</p>
          <ul className="usage-warnings">
            {detail.warnings.map((warning, index) => (
              <li key={`${index}:${warning}`}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {turns.length === 0 ? (
        <p className="state state--meta">这个会话没有逐轮记录。</p>
      ) : (
        <>
          <div className="usage-spark" role="img" aria-label={`逐轮 token 条形图，共 ${turns.length} 轮`}>
            {turns.map((turn) => (
              <span
                key={turn.index}
                className="usage-spark__bar"
                title={`第 ${turn.index} 轮: ${turn.total_tokens} token`}
                style={{ height: `${Math.max(3, Math.round((turn.total_tokens / max) * 100))}%` }}
              />
            ))}
          </div>
          <TableScroll label="该会话的逐轮明细">
            <Table aria-label="该会话的逐轮明细">
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">轮次</TableHead>
                  <TableHead scope="col">时间</TableHead>
                  <TableHead scope="col">模型</TableHead>
                  <TableHead scope="col" className="usage-num">
                    输入
                  </TableHead>
                  <TableHead scope="col" className="usage-num">
                    输出
                  </TableHead>
                  <TableHead scope="col" className="usage-num">
                    缓存读
                  </TableHead>
                  <TableHead scope="col" className="usage-num">
                    缓存写
                  </TableHead>
                  <TableHead scope="col" className="usage-num">
                    总 token
                  </TableHead>
                  <TableHead scope="col" className="usage-num">
                    成本
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {turns.map((turn) => (
                  <TurnRow key={turn.index} turn={turn} />
                ))}
              </TableBody>
            </Table>
          </TableScroll>
        </>
      )}
    </>
  )
}

function TurnRow({ turn }: { turn: UsageTurn }): JSX.Element {
  const step = turn.step === null ? '' : ` · step ${turn.step}`
  return (
    <TableRow
      data-testid="usage-turn-row"
      title={turn.native_turn_id ?? undefined}
    >
      <TableCell>
        第 {turn.index} 轮{step}
      </TableCell>
      <TableCell>
        <time dateTime={turn.at ?? undefined}>{formatTimestamp(turn.at)}</time>
      </TableCell>
      <TableCell>{turn.model ?? '未知模型'}</TableCell>
      <TableCell className="usage-num" title={String(turn.input_tokens)}>
        {formatTokens(turn.input_tokens)}
      </TableCell>
      <TableCell className="usage-num" title={String(turn.output_tokens)}>
        {formatTokens(turn.output_tokens)}
      </TableCell>
      <TableCell className="usage-num">{formatTokens(turn.cached_read_tokens)}</TableCell>
      <TableCell className="usage-num">{formatTokens(turn.cache_write_tokens)}</TableCell>
      <TableCell className="usage-num" title={String(turn.total_tokens)}>
        {formatTokens(turn.total_tokens)}
      </TableCell>
      <TableCell className="usage-num">{formatCost(turn.cost_usd)}</TableCell>
    </TableRow>
  )
}
