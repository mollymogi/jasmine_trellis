import { useEffect, useState } from 'react'
import type { Review } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import type { WriteAction } from '../app/useWriteAction'
import { conventionActions, reviewAgent, reviewItems, reviewKind, reviewReason } from '../lib/reviewPresentation'
import { formatTimestamp, maskCredentials } from '../lib/text'
import { statusLabel } from '../lib/statusLabels'
import { AgentLogo } from './AgentLogo'
import { RawText } from './RawText'
import { ErrorPanel } from './States'
import { JsonBlock } from './JsonBlock'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

export function HumanReview({ item, onReadback, approve, reject }: { item: Review; onReadback: () => void; approve: WriteAction; reject: WriteAction }) {
  const { endpoints } = useSession()
  const source = useResource(signal => endpoints.getEvent(item.resolution.source_event_id, signal), [item.resolution.source_event_id])
  const event = source.data?.event
  const projectId = event?.project_id ?? reviewItems(item)[0]?.candidate.scope.project_id
  const project = useResource(projectId ? signal => endpoints.getProject(projectId, signal) : null, [projectId])
  const hosts = useResource(signal => endpoints.listHosts(signal), [])
  const [host, setHost] = useState('')
  const hostId = host || (hosts.data?.hosts.some(h => h.host_id === event?.host_id) ? event!.host_id : hosts.data?.hosts[0]?.host_id) || ''
  const agent = reviewAgent(event)
  const [decision, setDecision] = useState<'approve' | 'reject' | null>(null)
  const [reason, setReason] = useState('')
  const action = decision === 'approve' ? approve : reject
  const busy = approve.busy || reject.busy
  const submitting = approve.state === 'submitting' || reject.state === 'submitting'
  const unresolved = approve.state === 'unknown' || reject.state === 'unknown'
  const actions = conventionActions(item, event)
  useEffect(() => { if (approve.result || reject.result) onReadback() }, [approve.result, reject.result])
  async function submit() {
    if (!reason.trim() || !hostId || item.status !== 'PENDING' || unresolved || busy) return
    const body = { host_id: hostId, expected_revision: item.revision, reason: reason.trim() }
    if (decision === 'approve' && actions && item.preview) await approve.submit({ ...body, actions, expected_revisions: item.preview.expected_revisions, expected_context_digest: item.preview.expected_context_digest })
    else if (decision === 'reject') await reject.submit(body)
  }
  return <>
    <div className="review-detail-meta"><Badge variant={item.status === 'PENDING' ? 'warning' : 'outline'}>{statusLabel('review', item.status)}</Badge><span className="processing-agent"><AgentLogo agent={agent.id} size={20} />{agent.name}</span><span>{maskCredentials(project.data?.project.name ?? (projectId ? '项目名称待确认' : '未关联项目')).text}</span><time>{formatTimestamp(item.created_at)}</time></div>
    <div className="review-explanation"><strong>为什么需要你确认</strong><p>{reviewReason(item)}</p></div>
    <section className="review-proposal" aria-label="拟采用的内容"><h3>建议采用的内容</h3>{reviewItems(item).length ? reviewItems(item).map((p, index) => <article key={p.candidate_index}><div className="review-proposal-heading"><b>{index + 1}. {reviewKind(p.candidate.kind)}</b><span>{p.candidate.scope.kind === 'PROJECT' ? '整个项目' : p.candidate.scope.kind === 'TASK' ? '指定任务' : p.candidate.scope.kind === 'GLOBAL' ? '所有项目' : '范围待明确'}</span>{p.candidate.certainty !== 'EXPLICIT' ? <Badge variant="warning">原意需确认</Badge> : null}{p.candidate.impact === 'HIGH' ? <Badge variant="warning">影响较大</Badge> : null}</div><RawText value={p.candidate.content} label="建议内容" appearance="chat" /><details><summary>核对这条建议对应的原话</summary><RawText value={p.candidate.source_span.quote} label="引用原话" appearance="chat" /><p className="muted">引用由提炼结果提供，可与下方完整来源对照。</p></details></article>) : <p>当前计划内容不可用，不能将它当成无需处理。</p>}</section>
    <details className="review-source"><summary>查看完整来源消息</summary>{source.error ? <ErrorPanel error={source.error} what="来源消息" onRetry={source.reload} /> : event ? <><p className="muted">{event.actor_kind === 'human' ? '用户消息' : '系统或 Agent 消息'} · {formatTimestamp(event.occurred_at)}</p><RawText value={typeof event.payload.text === 'string' ? event.payload.text : '此来源为结构化事件，请查看技术记录。'} label="完整来源" appearance="chat" /></> : <p>正在读取来源…</p>}<Button asChild size="sm" variant="ghost"><a href={event?.session_id ? sourceHref.conversation(event.project_id, event.task_id, event.session_id) : sourceHref.event(item.resolution.source_event_id)}>回到来源对话</a></Button></details>
    {item.preview_error ? <div className="review-conflict" role="alert"><strong>预览冲突，暂不能批准</strong><p>提炼内容或关联状态已变化，需要刷新并重新核对。这不是空计划，也不表示无需处理。</p><details><summary>冲突详情</summary><code>{item.preview_error.code}</code><JsonBlock value={item.preview_error.details} label="服务端冲突信息" /></details></div> : null}
    {item.status === 'PENDING' ? <>
      <div className="review-consequence"><h3>采用后会发生什么</h3><p>{actions ? '将以上内容保存为本项目已批准的上下文约定。不会自动生成禁止执行规则；记忆写入和 Agent 接收情况需分别核对。' : '这条建议目前不能直接批准为项目约定。涉及任务安排、验收、修正或不明确的原意，需要先在来源对话中说清处理对象和要求，再重新提炼；也可由熟悉协议的维护者使用高级处理方案。'}</p></div>
      <div className="processing-actions">{actions && !item.preview_error ? <Button disabled={busy || unresolved} onClick={() => setDecision('approve')}>批准为项目约定</Button> : null}<Button variant="outline" disabled={busy || unresolved} onClick={() => setDecision('reject')}>不采纳这条建议</Button>{!decision ? <Button asChild variant="ghost"><a href={sourceHref.reviews(null)}>暂不处理</a></Button> : null}</div>
      {decision ? <div className="review-confirm" aria-label="确认审核操作"><h3>{decision === 'approve' ? '确认采用为项目约定' : '确认不采纳'}</h3><p>{decision === 'approve' ? '只采用上方展示的内容和范围，不会修改原始消息。' : '拒绝后会移出待审核队列；原始对话和提炼记录仍保留。'}</p><label>处理说明<textarea aria-label="处理说明" rows={2} placeholder="写下采用或不采纳的原因" value={reason} disabled={busy || unresolved} onChange={e => setReason(e.target.value)} /></label><label>记录操作的设备<select aria-label="记录操作的设备" value={hostId} disabled={busy || unresolved} onChange={e => setHost(e.target.value)}>{hosts.data?.hosts.map(h => <option key={h.host_id} value={h.host_id}>{h.display_name || '本机设备'}</option>)}</select></label>{hosts.error ? <ErrorPanel error={hosts.error} what="设备信息" onRetry={hosts.reload} /> : null}<div className="processing-actions"><Button disabled={busy || unresolved || !reason.trim() || !hostId || (decision === 'approve' && !actions) || action.state === 'succeeded'} onClick={() => void submit()}>{submitting ? '正在提交…' : decision === 'approve' ? '确认批准' : '确认不采纳'}</Button><Button variant="ghost" disabled={busy || unresolved} onClick={() => setDecision(null)}>取消</Button></div></div> : null}
    </> : <p className="rules-note">这条建议{statusLabel('review', item.status)}，不再接受重复审核。<a href={sourceHref.reviews(null)}>返回待审核</a></p>}
    {action.state === 'conflict' ? <p role="alert">审核依据已变化，操作未自动重试。请刷新后重新核对；处理说明仍保留。</p> : action.state === 'refused' ? <p role="alert">本次操作未获准，请核对当前状态、设备和审核权限。</p> : unresolved ? <p role="alert">提交结果尚未确认，请刷新核对当前状态，暂勿再次提交或改做其他操作。</p> : action.state === 'succeeded' ? <p role="status">服务端已处理，正在读取审核结果。</p> : null}
    {action.error ? <details><summary>操作诊断</summary><JsonBlock value={{ code: action.error.code, request_id: action.requestId, details: action.error.details }} label="审核操作结果" /></details> : null}
  </>
}
