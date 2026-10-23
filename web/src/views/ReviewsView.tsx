/** Pending queue stays authoritative and cursor-paged; presentation uses content, not record IDs. */
import { useState } from 'react'
import { ClipboardCheck, RefreshCw, Search } from 'lucide-react'
import { DEFAULT_OBJECT_LIMIT } from '../api/endpoints'
import type { Project, RawEvent, Review } from '../api/types'
import { hrefFor, sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { afterIdLoader, usePagedResource } from '../app/usePagedResource'
import { useResource } from '../app/useResource'
import { Button } from '../components/ui/button'
import { Badge } from '../components/ui/badge'
import { AgentLogo } from '../components/AgentLogo'
import { ErrorPanel } from '../components/States'
import { reviewAgent, reviewItems, reviewKind, reviewReason, reviewSummary } from '../lib/reviewPresentation'
import { processingTime } from '../lib/processingPresentation'
import { formatTimestamp, maskCredentials } from '../lib/text'

const LIMIT = DEFAULT_OBJECT_LIMIT
export function ReviewsView({ projectId, taskId }: { projectId: string | null; taskId: string | null }): JSX.Element {
  const { endpoints } = useSession()
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState('')
  const reviews = usePagedResource(afterIdLoader<Review>({ limit: LIMIT, fetch: async (afterId, limit, signal) => {
    const page = await endpoints.listPendingReviews({ project_id: projectId, task_id: taskId, after_id: afterId, limit }, signal)
    return { items: page.items, nextAfterId: page.next_after_id }
  } }), [projectId, taskId])
  const projects = usePagedResource<Project>(async (cursor, signal) => {
    const offset = Number(cursor ?? 0), p = await endpoints.listProjects({ limit: 100, offset }, signal)
    return { items: p.projects, count: p.count, complete: p.count < 100, nextCursor: p.count < 100 ? null : offset + 100 }
  }, [])
  // Source names are decoration only. A missing source never fabricates an Agent or clears a pending item.
  // Bound fan-out to four reads, cancel on scope changes, and never process or write an Event here.
  const ids = [...new Set(reviews.items.map(r => r.resolution.source_event_id))]
  const sources = useResource(ids.length ? async signal => {
    const result: Record<string, RawEvent | null> = {}
    for (let i = 0; i < ids.length && !signal.aborted; i += 4) {
      await Promise.all(ids.slice(i, i + 4).map(async id => {
        try { result[id] = (await endpoints.getEvent(id, signal)).event } catch { result[id] = null }
      }))
    }
    return result
  } : null, [ids, projectId, taskId, reviews.loadedAt])
  const projectName = (r: Review) => {
    const id = sources.data?.[r.resolution.source_event_id]?.project_id ?? reviewItems(r)[0]?.candidate.scope.project_id
    return maskCredentials(projects.items.find(p => p.project_id === id)?.name ?? (id ? '项目名称待确认' : '未关联项目')).text
  }
  const kinds = [...new Set(reviews.items.flatMap(r => reviewItems(r).map(i => i.candidate.kind)))]
  const visible = reviews.items.filter(r => (!kind || reviewItems(r).some(i => i.candidate.kind === kind)) && `${reviewSummary(r)} ${projectName(r)} ${reviewReason(r)}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  return <section className="reviews-page" aria-label="待审核建议">
    <div className="processing-heading"><div><h2><ClipboardCheck size={18} />待审核<span className="processing-heading-count">已载入 {reviews.items.length} 条</span></h2><p>核对从对话中提炼的建议，由你决定是否采用。</p></div><Button size="sm" variant="outline" disabled={reviews.loading} onClick={() => { reviews.reload(); projects.reload() }}><RefreshCw size={14} />刷新</Button></div>
    <div className="rules-toolbar"><label className="rules-search"><Search size={15} /><span className="sr-only">搜索已载入建议</span><input placeholder="搜索内容或项目" value={search} onChange={e => setSearch(e.target.value)} /></label><select aria-label="审核项目" value={projectId ?? ''} onChange={e => { setSearch(''); setKind(''); globalThis.location.hash = hrefFor({ name: 'reviews', projectId: e.target.value || null, taskId: null }) }}><option value="">全部项目</option>{projectId && !projects.items.some(p => p.project_id === projectId) ? <option value={projectId}>当前项目</option> : null}{projects.items.map(p => <option key={p.project_id} value={p.project_id}>{maskCredentials(p.name).text}</option>)}</select><select aria-label="建议类型" value={kind} onChange={e => setKind(e.target.value)}><option value="">全部类型</option>{kinds.map(k => <option key={k} value={k}>{reviewKind(k)}</option>)}</select></div>
    {taskId ? <p className="muted">当前仅显示指定任务的建议。<a href={hrefFor({ name: 'reviews', projectId, taskId: null })}>查看项目下全部任务</a></p> : null}
    {projects.error ? <p className="muted">项目名称暂时无法读取，审核建议仍保留。<Button variant="ghost" size="sm" onClick={projects.reload}>重试项目名称</Button></p> : null}
    {projects.hasMore ? <Button variant="ghost" size="sm" disabled={projects.loading} onClick={projects.loadMore}>载入更多项目名称</Button> : null}
    {reviews.error ? <ErrorPanel error={reviews.error} what="待审核建议" onRetry={reviews.reload} /> : null}
    {reviews.loading && !reviews.items.length ? <p role="status" className="rules-empty">正在读取待审核建议…</p> : !reviews.error && !visible.length ? <div className="rules-empty"><ClipboardCheck size={28} /><h3>{search || kind ? '已载入的建议中没有匹配内容' : '当前范围没有待审核建议'}</h3><p>{search || kind ? '试试其他关键词或类型，也可以继续载入。' : '这里只显示尚未处理的建议，已批准和已拒绝的记录不在此列表。'}</p></div> : null}
    {visible.length ? <div className="reviews-table"><table aria-label="待审核建议列表"><thead><tr><th>序号</th><th>建议内容</th><th>项目 / Agent</th><th>时间</th><th>审核原因</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{visible.map(r => {
      const agent = reviewAgent(sources.data?.[r.resolution.source_event_id])
      return <tr key={r.review_id}><td data-label="序号">{reviews.items.indexOf(r) + 1}</td><td data-label="建议内容"><a className="rule-content" href={sourceHref.review(r.review_id)}>{reviewSummary(r)}</a><div className="review-row-tags">{[...new Set(reviewItems(r).map(i => i.candidate.kind))].map(k => <span key={k}>{reviewKind(k)}</span>)}{r.preview_error ? <Badge variant="destructive">预览冲突</Badge> : <Badge variant="warning">待审核</Badge>}</div></td><td data-label="项目 / Agent"><span className="rule-project">{projectName(r)}</span><span className="processing-agent rule-secondary"><AgentLogo agent={agent.id} size={17} />{agent.name}</span></td><td data-label="时间"><time dateTime={r.created_at} title={formatTimestamp(r.created_at)}>{processingTime(r.created_at)}</time></td><td data-label="审核原因">{r.preview_error ? '内容已变化，请重新核对' : reviewReason(r)}</td><td data-label="操作"><Button asChild size="sm" variant="outline"><a href={sourceHref.review(r.review_id)}>查看建议</a></Button></td></tr>
    })}</tbody></table></div> : null}
    <div className="review-list-footer"><span>已载入 {reviews.items.length} 条{search || kind ? `，匹配 ${visible.length} 条` : ''} · {reviews.loadedAt ? `更新于 ${processingTime(reviews.loadedAt)}` : '正在读取'}</span>{reviews.hasMore ? <Button variant="outline" disabled={reviews.loading} onClick={reviews.loadMore}>载入更多建议</Button> : reviews.complete ? <span>当前范围没有更多待审核建议</span> : null}</div>
    <details className="reviews-help"><summary>这些建议怎么处理？</summary><p>先查看原话和拟采用的内容。明确的项目约定可批准为上下文规则；涉及任务、验收或不明确的要求，需要先明确处理方案。不合适的建议可拒绝，暂不处理则保留在队列中。</p><p>批准不等于记忆已写入或 Agent 已收到要求。记忆写入结果请到记忆页核对；执行拦截还需单独接入 Guard。</p><details><summary>技术说明与读取范围</summary><p>只读取服务端 PENDING 队列，按 after_id 分页；已载入数不是总数。详情读取可能完成服务端待处理的恢复操作；本页不会自动提交审核。</p><p>项目：{projectId ?? '全部'}；任务：{taskId ?? '全部'}。人工重新应用决议、原生 Evidence 来源确认尚不在此页面交付。</p></details></details>
  </section>
}
