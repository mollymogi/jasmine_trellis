import type { ContextPack } from '../api/types'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { SourceLink } from '../components/Trace'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatTimestamp } from '../lib/text'

export interface Delivery {
  context_pack_id: string
  status: string
  historical: boolean
  current_use_proved: boolean
  trust_boundary: string
  record: null | {
    native_thread_id: string; native_turn_id: string; source_event_id: string
    rendered_sha256: string; observed_at: string; native_rollout_sha256: string; receipt_sha256: string
  }
}

export function ContextDelivery({ pack }: { pack: ContextPack }) {
  const { endpoints } = useSession()
  const resource = useResource(signal => endpoints.getContextDelivery(pack.context_pack_id, signal), [pack.context_pack_id])
  return <ContextDeliveryCard pack={pack} delivery={resource.data?.delivery ?? null}
    loading={resource.status === 'loading'} failed={resource.status === 'error'} />
}

export function ContextDeliveryCard({ pack, delivery, loading = false, failed = false }: {
  pack: ContextPack; delivery: Delivery | null; loading?: boolean; failed?: boolean
}) {
  const valid = delivery?.context_pack_id === pack.context_pack_id && delivery.historical === true &&
    delivery.current_use_proved === false && delivery.trust_boundary === 'operator_owned_native_collector'
  const record = valid && delivery.status === 'MATCHED_NATIVE_INPUT' ? delivery.record : null
  const matched = record != null && typeof record === 'object' && record.rendered_sha256 === pack.rendered_sha256 &&
    record.source_event_id === pack.source_event_id && typeof record.native_thread_id === 'string' &&
    typeof record.native_turn_id === 'string' && typeof record.observed_at === 'string' &&
    Number.isFinite(Date.parse(record.observed_at)) &&
    /^[0-9a-f-]{36}$/.test(record.native_thread_id) && /^[0-9a-f-]{36}$/.test(record.native_turn_id) &&
    /^[0-9a-f]{64}$/.test(record.receipt_sha256) && /^[0-9a-f]{64}$/.test(record.native_rollout_sha256)
  const state = loading ? 'loading' : failed ? 'error' : matched ? 'matched' :
    valid && ['NO_RECORD', 'UNAVAILABLE'].includes(delivery.status) ? 'missing' : 'invalid'
  const title = { loading: '正在读取接收记录', error: '接收记录暂时无法读取',
    matched: '已记录 Codex 接收凭证', missing: '尚无可核对的接收记录', invalid: '接收记录无法核对' }[state]
  return <Card data-testid="context-delivery" data-status={state}>
    <CardHeader><CardTitle className="text-base">{title}</CardTitle></CardHeader>
    <CardContent className="grid gap-3 text-sm">
      <p>{state === 'matched' ? '这次隔离验收中，原生输入与这份上下文逐字一致。' :
        state === 'loading' ? '正在向 Core 核对这份上下文的独立接收记录。' :
        state === 'missing' ? '目前只能确认上下文已构建，尚不能确认 Codex 收到了它。' :
        '读取或核对没有完成，暂不能确认 Codex 收到了这份上下文。'}</p>
      <p className="text-xs text-muted-foreground">接收凭证是宿主采集工具核对的历史记录，不代表当前轮次仍在使用，也不代表独立验收已签署。</p>
      {matched && record ? <details>
        <summary className="cursor-pointer text-primary">查看接收细节</summary>
        <dl className="mt-3 grid gap-2 break-all text-xs">
          <div><dt>核对时间</dt><dd>{formatTimestamp(record.observed_at)}</dd></div>
          <div><dt>原生会话</dt><dd>{record.native_thread_id}</dd></div>
          <div><dt>原生轮次</dt><dd>{record.native_turn_id}</dd></div>
          <div><dt>来源</dt><dd><SourceLink kind="event" id={record.source_event_id} /></dd></div>
          <div><dt>上下文内容指纹</dt><dd>{record.rendered_sha256}</dd></div>
          <div><dt>采集记录指纹</dt><dd>{record.receipt_sha256}</dd></div>
        </dl>
      </details> : null}
    </CardContent>
  </Card>
}
