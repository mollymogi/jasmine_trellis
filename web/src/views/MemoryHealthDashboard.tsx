import { useRef, useState, type ReactNode } from 'react'
import { ArrowRight, BookOpen, CheckCircle2, CircleHelp, FileText, History, RefreshCw, ShieldCheck, TriangleAlert, Unplug } from 'lucide-react'
import type { MemoryHealth } from '../api/types'
import type { MemoryEngineState } from '../api/memoryEngine'
import { formatTimestamp } from '../lib/text'
import { DetailLayout } from '@/components/ui/detail-layout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { DescriptionItem, DescriptionList } from '@/components/ui/description-list'
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'

const STATES: Record<string, { title: string; description: string; label: string }> = {
  unconfigured: { title: '尚未配置 Memory 引擎', description: '已有记忆记录仍可查看；当前没有配置可用的记忆引擎。', label: '未配置' },
  idle: { title: '引擎已配置，尚无成功操作记录', description: '还不能据此确认记忆写入或召回已经可用。', label: '待验证' },
  ok: { title: 'Memory 最近运行正常', description: '最近记录未报告故障；这不代表每条记忆都适用于当前任务。', label: '运行正常' },
  unreachable: { title: 'Memory 引擎暂时无法连接', description: '记忆处理已暂停，可以查看已有记录和错误详情。', label: '连接异常' },
  failing: { title: '有记忆操作处理失败', description: '请查看失败任务和运行详情，确认需要处理的问题。', label: '需要处理' },
  degraded: { title: '还有记忆操作等待处理', description: '部分记忆操作仍在队列中，当前结果可能尚未更新。', label: '处理中' },
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

/** Reads Core's report; no engine probe, configuration change or inferred success. */
export function MemoryHealthDashboard({ health, onRefresh, details, engineControl, engineState }: {
  health: MemoryHealth; onRefresh: () => void; details: ReactNode; engineControl?: ReactNode; engineState?: MemoryEngineState | null
}): JSX.Element {
  const [open, setOpen] = useState(false)
  const [container, setContainer] = useState<HTMLDivElement | null>(null)
  const trigger = useRef<HTMLButtonElement | null>(null)
  const known = Object.hasOwn(STATES, health.status)
  // A running process is service readiness, never proof of a successful memory operation.
  // Keep Core's failure, paused and unknown verdicts prominent even if the process lives.
  const ready = engineState?.state === 'RUNNING' && health.status === 'idle' && health.configured === true && health.reachable === true && health.core_impact === 'none'
  const state = ready ? { title: 'Hindsight 已启动', description: '引擎已连接；记忆写入和召回尚未验证，目前没有成功操作记录。', label: '引擎运行中' } : known ? STATES[health.status]! : { title: 'Memory 状态暂时无法确认', description: '当前报告没有可识别的运行结论，请查看详情核对。', label: '待核对' }
  const warning = ['unreachable', 'failing', 'degraded'].includes(health.status)
  const Icon = warning ? TriangleAlert : ready || health.status === 'ok' ? CheckCircle2 : health.status === 'unconfigured' ? Unplug : CircleHelp
  const pending = count(health.jobs.pending), running = count(health.jobs.running)
  const active = count(health.items.items_active), failed = count(health.jobs.failed)
  const processing = pending === null || running === null ? null : pending + running
  const display = (value: unknown): string => count(value)?.toString() ?? '未提供'
  return <div ref={setContainer} data-testid="memory-health-dashboard">
    <DetailLayout
      header={<div className="flex flex-wrap items-start justify-between gap-4">
        <div className="grid gap-2"><p className="text-xs font-medium tracking-widest text-muted-foreground">MEMORY / 运行概览</p><h1 className="text-2xl font-semibold tracking-tight">Memory 概览</h1></div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={onRefresh}><RefreshCw />刷新状态</Button>
          <Button variant="outline" onClick={event => { trigger.current = event.currentTarget; setOpen(true) }}><FileText />查看运行详情</Button>
        </div>
      </div>}
      summary={<Card className={warning ? 'border-warning/40 bg-warning-soft/30' : 'border-primary/30 bg-accent/20'}>
        <CardHeader className="gap-4"><Badge variant={warning ? 'warning' : ready ? 'success' : 'outline'}><Icon />{state.label}</Badge>
          <CardTitle className="text-2xl leading-snug sm:text-3xl">{state.title}</CardTitle>
          <CardDescription className="text-sm leading-relaxed text-foreground" role={warning ? 'alert' : 'status'}>{state.description}</CardDescription>
        </CardHeader>
        <CardContent className="border-t border-primary/15 pt-4 text-sm text-muted-foreground">
          {engineControl}
          {health.core_impact === 'none' ? 'Core 主流程未受影响。' : health.core_impact === 'memory_paused' ? 'Memory 已暂停，Core 主流程仍可使用。' : '对 Core 的影响尚待核对。'}
          {health.non_authoritative === true ? ' 记忆仅供历史参考，当前规则和任务状态优先。' : null}
        </CardContent>
      </Card>}
      metrics={[
        { label: '保留中的记忆', value: active, note: '保留状态不代表当前有效规则' },
        { label: '待处理任务', value: processing, note: `${pending ?? '未知'} 等待中 · ${running ?? '未知'} 正在处理` },
        { label: '失败任务', value: failed, note: '队列中记录的失败任务数' },
      ].map(metric => <Card key={metric.label} className="gap-2 py-4"><CardContent className="grid gap-2">
        <p className="text-xs text-muted-foreground">{metric.label}</p><p className="text-3xl font-semibold">{metric.value ?? '未提供'}</p><p className="text-xs text-muted-foreground">{metric.note}</p>
      </CardContent></Card>)}
      aside={<Card><CardHeader><CardTitle className="text-sm">处理概况</CardTitle></CardHeader><CardContent><DescriptionList compact>
        <DescriptionItem term="已完成任务">{display(health.jobs.done)}</DescriptionItem>
        <DescriptionItem term="已过期记忆">{display(health.items.items_obsolete)}</DescriptionItem>
        <DescriptionItem term="已删除记忆">{display(health.items.items_deleted)}</DescriptionItem>
        <DescriptionItem term="最近成功操作">{health.last_success_at ? formatTimestamp(health.last_success_at) : '尚无记录'}</DescriptionItem>
      </DescriptionList></CardContent></Card>}
    >
      <Card><CardHeader><CardTitle>查看记忆与来源</CardTitle><CardDescription>从具体内容出发，核对历史记忆与当前规则。</CardDescription></CardHeader>
        <CardContent className="grid gap-4">
          <Button asChild className="w-fit"><a href="#/memory/items"><BookOpen />查看记忆条目<ArrowRight /></a></Button>
          <p className="text-sm text-muted-foreground">打开一条记忆，可查看当前要求、历史来源和变化依据。</p>
          <div className="flex flex-wrap gap-3 border-t pt-4">
            <Button asChild variant="outline"><a href="#/memory/jobs"><RefreshCw />查看处理队列</a></Button>
            <Button asChild variant="outline"><a href="#/memory/traces"><History />查看召回记录</a></Button>
            <Button asChild variant="ghost"><a href="#/rules"><ShieldCheck />查看当前规则</a></Button>
          </div>
        </CardContent>
      </Card>
    </DetailLayout>
    <Sheet open={open} onOpenChange={setOpen}><SheetContent container={container} className="sm:max-w-2xl" onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus() }}>
      <SheetHeader><SheetTitle>Memory 运行详情</SheetTitle><SheetDescription>原始运行字段、完整计数和功能说明。查看不会修改配置或启动引擎。</SheetDescription></SheetHeader>
      <SheetBody className="grid content-start gap-6" data-testid="memory-health-details">{details}</SheetBody>
    </SheetContent></Sheet>
  </div>
}
