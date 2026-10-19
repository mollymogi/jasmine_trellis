/** ADR 0022: Core owns the comparison; mogi-ui provides the dashboard hierarchy. */
import { useState, type ReactNode } from 'react'
import { ArrowRight, BookOpen, CircleHelp, FileText, History, Link2, LockKeyhole, ShieldCheck, TriangleAlert } from 'lucide-react'
import type { MemoryAuthorityComparison } from '../api/types'
import { SourceLink } from '../components/Trace'
import { RawText } from '../components/RawText'
import { formatTimestamp, clip, maskCredentials } from '../lib/text'
import { isCoreId } from '../lib/ids'
import { hrefFor } from '../app/router'
import { DetailLayout } from '@/components/ui/detail-layout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet'

const COPY: Record<string, { title: string; description: string; badge: string }> = {
  VERSION_CHANGED: { title: '当前规则已更新', description: '这条记忆中的决策已被新规则替代，仅供历史参考。', badge: '已替代' },
  ORIGIN_RETIRED: { title: '来源规则已撤销', description: '这条记忆的来源规则已撤销，请勿作为当前依据。', badge: '已撤销' },
  VERSION_MATCH: { title: '关联规则版本未变化', description: '版本相同不代表语义一致或验证通过，记忆仍仅供参考。', badge: '版本未变' },
  UNVERIFIABLE: { title: '这条记忆还无法核对', description: '缺少可靠的关联，暂时无法核对这条记忆。', badge: '待核对' },
  UNAVAILABLE: { title: '暂无权限查看规则对照', description: '当前权限不足，无法查看这条记忆与规则的对照。', badge: '权限不足' },
  NOT_APPLICABLE: { title: '这是一条历史参考', description: '此记忆不适用决策版本对照，不能据此判断没有语义冲突。', badge: '参考信息' },
}

function ComparisonText({ value }: { value: string }): JSX.Element {
  return <p className="whitespace-pre-wrap break-words text-base leading-relaxed">{clip(maskCredentials(value).text, 480) || '（空内容）'}</p>
}

export function MemoryAuthority({ comparison, aside, actions }: {
  comparison?: MemoryAuthorityComparison; aside?: ReactNode; actions?: ReactNode
}): JSX.Element {
  const [open, setOpen] = useState(false)
  const [container, setContainer] = useState<HTMLDivElement | null>(null)
  const current = comparison?.current_rule
  const historical = comparison?.historical_rule
  const paired = comparison && ['VERSION_CHANGED', 'ORIGIN_RETIRED', 'VERSION_MATCH'].includes(comparison.status)
  const validPair = current && historical && isCoreId(current.rule_id, 'rul') &&
    Number.isInteger(current.version) && Number.isInteger(historical.version) &&
    current.version > 0 && historical.version > 0 && typeof current.content === 'string' &&
    typeof historical.content === 'string' && current.scope && typeof current.scope === 'object' &&
    Number.isInteger(current.revision) && current.revision > 0 &&
    isCoreId(current.origin_event_id, 'evt') && isCoreId(historical.origin_event_id, 'evt') && (
      comparison?.status === 'ORIGIN_RETIRED' ? current.status === 'RETIRED' :
      current.status === 'ACTIVE' && (comparison?.status === 'VERSION_MATCH'
        ? current.version === historical.version : current.version !== historical.version)
    )
  const known = comparison && Object.hasOwn(COPY, comparison.status) && (!paired || validPair)
  const status = known ? comparison.status : 'UNKNOWN'
  const copy = known ? COPY[status]! : { title: '暂时无法核对', description: 'Core 未提供可识别的完整结果，不能据此判断没有冲突。', badge: '信息不足' }
  const warning = status === 'VERSION_CHANGED' || status === 'ORIGIN_RETIRED'
  const Icon = warning ? TriangleAlert : status === 'UNAVAILABLE' ? LockKeyhole : status === 'VERSION_MATCH' ? Link2 : CircleHelp
  return (
    <div ref={setContainer} data-testid="memory-authority" aria-label="历史 Memory 与当前 Authority">
      <DetailLayout
        header={<div className="flex flex-wrap items-start justify-between gap-4">
          <div className="grid gap-2">
            <p className="text-xs font-medium tracking-widest text-muted-foreground"><a href="#/memory/items">MEMORY</a> / 决策对照</p>
            <h1 className="text-2xl font-semibold tracking-tight">记忆详情</h1>
          </div>
          <div className="flex flex-wrap gap-2">{actions}<Button variant="outline" onClick={() => setOpen(true)}><FileText /> 查看判定依据</Button></div>
        </div>}
        summary={<Card className={warning ? 'border-warning/40 bg-warning-soft/30' : ''}>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant={warning ? 'warning' : 'outline'}><Icon /> {copy.badge}</Badge>
              <span className="text-xs text-muted-foreground">以当前规则为准</span>
            </div>
            <CardTitle className="text-2xl leading-snug">{copy.title}</CardTitle>
            <CardDescription role={warning ? 'alert' : 'status'} data-testid="memory-authority-status" data-status={status} className="text-sm leading-relaxed text-foreground">{copy.description}</CardDescription>
          </CardHeader>
        </Card>}
        metrics={[
          { label: '历史来源版本', value: paired && validPair ? `v${historical.version}` : '待核对', Icon: History },
          { label: '当前规则版本', value: paired && validPair ? `v${current.version}` : '待核对', Icon: ShieldCheck },
          { label: '记忆定位', value: '历史参考', Icon: BookOpen },
        ].map(({ label, value, Icon: MetricIcon }) => <Card key={label} className="gap-2 py-4"><CardContent className="grid gap-2">
          <p className="flex items-center gap-2 text-xs text-muted-foreground"><MetricIcon className="size-4" />{label}</p>
          <p className="text-xl font-semibold">{value}</p>
        </CardContent></Card>)}
        aside={aside}
      >
        <Card>
          <CardHeader><CardTitle>历史与当前</CardTitle><CardDescription>{paired && validPair ? '保留当时的内容，同时查看本次采样读到的规则。' : '缺少可靠关联时，不把历史内容判定为当前依据。'}</CardDescription></CardHeader>
          <CardContent>
            {paired && validPair ? <div className="grid gap-4 md:grid-cols-2" data-testid="memory-authority-comparison">
              <div className="grid content-start gap-4 rounded-lg border bg-muted/40 p-4" data-testid="memory-history-card">
                <div className="flex items-center justify-between gap-2"><span className="text-sm text-muted-foreground">历史来源</span><Badge variant="outline"><History /> v{historical.version}</Badge></div>
                <ComparisonText value={historical.content} />
                <p className="text-xs text-muted-foreground">仅供追溯，不会覆盖当前规则。</p>
              </div>
              <div className="grid content-start gap-4 rounded-lg border border-primary/30 bg-accent/30 p-4" data-testid="memory-current-card">
                <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">当前规则记录</span><Badge variant="soft"><ShieldCheck /> v{current.version}</Badge></div>
                <ComparisonText value={current.content} />
                <p className="text-xs text-muted-foreground">{current.status === 'RETIRED' ? '已撤销，不是当前生效的规则。' : '本次采样时关联版本有效。'}</p>
              </div>
            </div> : <div className="grid gap-2 rounded-lg bg-muted/40 p-4"><p>暂不能展示版本对照。</p><p className="text-sm text-muted-foreground">此对照只核对明确关联的规则版本，不按文字判断任意语义冲突。</p></div>}
          </CardContent>
        </Card>
        <p className="flex items-center gap-2 text-xs text-muted-foreground"><History className="size-4 shrink-0" />只读快照；记忆、规则和任务状态的修改需要分别提交。</p>
      </DetailLayout>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent container={container} className="sm:max-w-xl">
          <SheetHeader><SheetTitle>对照依据与来源</SheetTitle><SheetDescription>完整版本、来源事件和采样信息。</SheetDescription></SheetHeader>
          <SheetBody className="grid content-start gap-4" data-testid="memory-authority-details">
            <p>判定：<code>{status}</code>；依据：<code>{comparison?.reason ?? 'comparison_unavailable'}</code>；Core 采样时间：{comparison ? formatTimestamp(comparison.sampled_at) : '未提供'}。</p>
            <p>关联版本相同不代表语义一致或验证通过。Memory 仅为历史参考，不能覆盖 Rule 或 Task State。ACTIVE / recallable 只表示生命周期状态，实际注入还需通过来源、当前规则和范围检查。</p>
            {status === 'UNAVAILABLE' ? <p>需要 authority:read；不会改用其他凭据。</p> : null}
            {!known ? <p>对照不可用：Core 未提供可识别的完整结果，不能据此判断没有冲突。</p> : null}
            {paired && validPair ? <>
              <SourceLink kind="rule" id={current.rule_id} projectId={current.scope.project_id} taskId={current.scope.task_id} label="读取最新 Rule / 版本历史" />
              <p data-testid="memory-authority-versions">历史来源 v{historical.version} → 当前记录 v{current.version} · r{current.revision} · {current.status}</p>
              <RawText value={historical.content} label="历史来源版本内容" machine={false} />
              <SourceLink kind="event" id={historical.origin_event_id} label="历史来源 Event" />
              <RawText value={current.content} label="当前规则记录内容" machine={false} />
              <SourceLink kind="event" id={current.origin_event_id} label="当前来源 Event" />
              {current.status_event_id ? <SourceLink kind="event" id={current.status_event_id} label="当前状态变更 Event" /> : null}
              <p>以上是本次读取的快照。打开 Rule 链接或重新读取后，当前版本可能已变化。</p>
            </> : null}
          </SheetBody>
          <SheetFooter><Button variant="outline" onClick={() => setOpen(false)}>返回看板 <ArrowRight /></Button></SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  )
}

export function HistoricalMemoryFilters({ filtered }: { filtered: unknown[] }): JSX.Element {
  const [open, setOpen] = useState(false)
  const [container, setContainer] = useState<HTMLDivElement | null>(null)
  const rows = filtered.filter((v): v is Record<string, unknown> => !!v && typeof v === 'object' &&
    'reason' in v && (v.reason === 'superseded_decision' || v.reason === 'retired_decision'))
  return <div ref={setContainer} data-testid="memory-authority-filters" className="grid gap-4">
    <Card><CardHeader><CardTitle>历史决策过滤</CardTitle><CardDescription role={rows.length ? 'alert' : 'status'}>
      {rows.length ? `这次召回过滤了 ${rows.length} 条已被替代或撤销的旧决策。` : '本次记录未报告因规则替代或撤销而过滤的决策，不代表已经检查了所有语义冲突。'}
    </CardDescription></CardHeader><CardContent><Button variant="outline" onClick={() => setOpen(true)}><History /> 查看过滤记录</Button></CardContent></Card>
    <Sheet open={open} onOpenChange={setOpen}><SheetContent container={container} className="sm:max-w-xl">
      <SheetHeader><SheetTitle>召回过滤记录</SheetTitle><SheetDescription>以下是召回当时记录的过滤结果；当前规则需打开对应 Memory 重新核对。</SheetDescription></SheetHeader>
      <SheetBody className="grid content-start gap-4" data-testid="memory-filter-details">{rows.map((row, index) => <div key={index} className="grid gap-2 rounded-lg border p-4"><code>{String(row.reason)}</code>
        {typeof row.memory_id === 'string' && isCoreId(row.memory_id, 'mem') ? <a href={hrefFor({ name: 'memoryItem', memoryId: row.memory_id })}>查看历史 Memory 与当前 Authority</a> : '记录未提供可追溯的 Memory ID'}
      </div>)}</SheetBody>
    </SheetContent></Sheet>
  </div>
}
