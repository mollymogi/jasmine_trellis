/**
 * Host-hook decisions, as Core recorded them (P1 enforcement view).
 *
 * Read-only. Each row is one decision Core computed for an agent's hook call: the verdict, the
 * Guard rules that matched, and the reason. Tool inputs and transcripts were never stored, so
 * they cannot appear here; the panel says so rather than leaving the reader to guess.
 */

import { useEffect, useRef, useState } from 'react'
import { CircleCheck, CircleHelp, CircleX, TriangleAlert } from 'lucide-react'

import type { HookDecision, HookDelivery } from '../api/types'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { formatTimestamp } from '../lib/text'
import { Empty, ErrorPanel, Loading, Panel } from './States'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { LoadMore } from './ui/load-more'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'

const FILTERS: { value: HookDecision | null; label: string }[] = [
  { value: null, label: '全部' },
  { value: 'deny', label: '拒绝' },
  { value: 'ask', label: '询问' },
  { value: 'allow', label: '放行' },
  { value: 'none', label: '无需判定' },
]

const DECISION: Record<
  string,
  { label: string; variant: 'destructive' | 'warning' | 'success' | 'soft'; icon: typeof CircleX }
> = {
  deny: { label: '拒绝', variant: 'destructive', icon: CircleX },
  ask: { label: '询问', variant: 'warning', icon: TriangleAlert },
  allow: { label: '放行', variant: 'success', icon: CircleCheck },
  none: { label: '无需判定', variant: 'soft', icon: CircleHelp },
}

export function HookDecisions(): JSX.Element {
  const { endpoints } = useSession()
  const [decision, setDecision] = useState<HookDecision | null>(null)
  const [older, setOlder] = useState<HookDelivery[]>([])
  const [olderCursor, setOlderCursor] = useState<number | null | undefined>(undefined)
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState<string | null>(null)
  // The in-flight "load more" request. Changing the filter aborts it, so a page fetched for the old
  // filter can never be appended to (or move the cursor of) the new one.
  const moreRequest = useRef<AbortController | null>(null)

  useEffect(() => () => moreRequest.current?.abort(), [])

  const first = useResource(
    (signal) => endpoints.listHookDeliveries({ decision, limit: 50 }, signal),
    [decision],
  )

  const choose = (next: HookDecision | null): void => {
    moreRequest.current?.abort()
    moreRequest.current = null
    setLoadingMore(false)
    setDecision(next)
    setOlder([])
    setOlderCursor(undefined)
    setMoreError(null)
  }

  const firstPage = first.data
  const cursor = olderCursor !== undefined ? olderCursor : firstPage?.next_before_seq ?? null
  const hasMore = olderCursor !== undefined ? olderCursor !== null : firstPage?.has_more ?? false
  const rows = [...(firstPage?.deliveries ?? []), ...older]

  const loadMore = async (): Promise<void> => {
    if (cursor === null) return
    moreRequest.current?.abort()
    const controller = new AbortController()
    moreRequest.current = controller
    setLoadingMore(true)
    setMoreError(null)
    try {
      const page = await endpoints.listHookDeliveries(
        { decision, before_seq: cursor, limit: 50 },
        controller.signal,
      )
      // Superseded (filter changed or unmounted) while the page was in flight: drop it.
      if (controller.signal.aborted) return
      setOlder((current) => [...current, ...page.deliveries])
      setOlderCursor(page.has_more ? page.next_before_seq : null)
    } catch (caught) {
      if (controller.signal.aborted) return
      setMoreError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (moreRequest.current === controller) {
        moreRequest.current = null
        setLoadingMore(false)
      }
    }
  }

  return (
    <Panel title="Hook 决定记录（Core 已记录）">
      <p className="state state--meta">
        这里只列出 Core 记录下来的 hook 决定：结论、命中的 Guard 规则和原因。工具输入与会话内容不会被读回。
        “Core 计算”是规则检查的决定；“Hook 上报”是可信 hook 的实际选择，本地拒绝可能没有命中规则。
        “拒绝”不等于 Agent 一定执行了拦截；执行结果以各自的 Evidence 为准。
      </p>
      <div className="filters" role="group" aria-label="按决定筛选">
        {FILTERS.map((item) => (
          <Button
            key={item.label}
            type="button"
            size="sm"
            variant={decision === item.value ? 'default' : 'outline'}
            aria-pressed={decision === item.value}
            onClick={() => choose(item.value)}
          >
            {item.label}
          </Button>
        ))}
      </div>

      {first.status === 'loading' && first.data === null ? <Loading what="Hook 决定" /> : null}
      {first.status === 'error' && first.error !== null ? (
        <ErrorPanel error={first.error} what="Hook 决定" onRetry={first.reload} />
      ) : null}
      {firstPage !== undefined && firstPage !== null && rows.length === 0 ? (
        <Empty what="Hook 决定" hint="Core 还没有记录这一类决定。" />
      ) : null}

      {rows.length > 0 ? (
        <>
          <div className="tablescroll" tabIndex={0} aria-label="Hook 决定列表">
            <Table data-testid="hook-decisions">
              <TableHeader>
                <TableRow>
                  <TableHead>时间</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>事件</TableHead>
                  <TableHead>决定</TableHead>
                  <TableHead>命中规则</TableHead>
                  <TableHead>原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => {
                  const spec = row.decision !== null ? DECISION[row.decision] : undefined
                  const Icon = spec?.icon ?? CircleHelp
                  return (
                    <TableRow key={row.delivery_id} data-decision={row.decision ?? 'unknown'}>
                      <TableCell className="whitespace-nowrap">{formatTimestamp(row.created_at)}</TableCell>
                      <TableCell>{row.agent}</TableCell>
                      <TableCell>
                        <code>{row.event}</code>
                        {row.event === 'tool.before' ? (
                          <div className="muted">{row.decision_source === 'adapter_report' ? 'Hook 上报' : 'Core 计算'}</div>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        <Badge variant={spec?.variant ?? 'soft'}>
                          <Icon aria-hidden="true" />
                          {spec?.label ?? row.decision ?? '未知'}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {row.matched_rules.length === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          row.matched_rules.map((rule, index) => (
                            <div key={`${row.delivery_id}-${index}`} className="mono">
                              {rule.rule_key ?? rule.rule_id ?? '规则'}
                              {rule.version !== null ? ` v${rule.version}` : ''}
                              {rule.enforcement !== null ? ` · ${rule.enforcement}` : ''}
                            </div>
                          ))
                        )}
                      </TableCell>
                      <TableCell className="wrap">{row.reason ?? <span className="muted">—</span>}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
          {moreError !== null ? <p className="state state--error">读取更多失败：{moreError}</p> : null}
          <LoadMore
            loaded={rows.length}
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={() => void loadMore()}
            loadMoreLabel="继续加载 Hook 决定"
          />
        </>
      ) : null}
    </Panel>
  )
}
