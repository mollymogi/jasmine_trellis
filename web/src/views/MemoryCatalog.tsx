import { useState } from 'react'
import { ArrowRight, BookOpen, CircleHelp, RefreshCw, Search } from 'lucide-react'
import type { MemoryItemListRow, Project } from '../api/types'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { usePagedResource } from '../app/usePagedResource'
import { hrefFor } from '../app/router'
import { Button } from '../components/ui/button'
import { Badge } from '../components/ui/badge'
import { ErrorPanel, Loading } from '../components/States'
import { isCoreId } from '../lib/ids'
import { maskCredentials, formatTimestamp } from '../lib/text'
import { processingTime } from '../lib/processingPresentation'
import { memoryPreview, memoryType, memoryState, memoryEngineState, MEMORY_TYPES } from '../lib/memoryPresentation'
import { MemoryDependencyPending, MemoryNotFound, MemorySliceStatus, isMemoryPending } from './MemoryDependency'

const LIMIT = 50
type Page = { items: MemoryItemListRow[]; count: number; non_authoritative?: unknown }
export function MemoryCatalog(): JSX.Element {
  const { endpoints } = useSession()
  const [project, setProject] = useState(''), [status, setStatus] = useState(''), [kind, setKind] = useState(''), [search, setSearch] = useState('')
  const [explain, setExplain] = useState(false), [lookup, setLookup] = useState('')
  // Keep page envelopes so Core's non_authoritative declaration survives pagination,
  // including an empty first/last page. A short page proves only this query is exhausted.
  const pages = usePagedResource<Page>(async (cursor, signal) => {
    const page = await endpoints.listMemoryItems({ project_id: project || null, status: status || null, memory_type: kind || null, limit: LIMIT, after_memory_id: cursor === null ? null : String(cursor) }, signal)
    const more = page.items.length === LIMIT
    return { items: [page], count: 1, complete: !more, nextCursor: more ? page.items.at(-1)!.memory_id : null }
  }, [project, status, kind])
  const projects = usePagedResource<Project>(async (cursor, signal) => {
    const offset = Number(cursor ?? 0), page = await endpoints.listProjects({ limit: 100, offset }, signal)
    return { items: page.projects, count: page.count, complete: page.count < 100, nextCursor: page.count < 100 ? null : offset + 100 }
  }, [])
  const items = pages.items.flatMap(p => p.items)
  const declaredReference = pages.items.length > 0 && pages.items.every(p => p.non_authoritative === true)
  const projectName = (item: MemoryItemListRow) => maskCredentials(item.scope_kind === 'global' ? '所有项目' : projects.items.find(p => p.project_id === item.project_id)?.name ?? '项目名称待确认').text
  const visible = items.filter(item => `${maskCredentials(item.sanitized_text ?? '').text} ${projectName(item)}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())).sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.created_at.localeCompare(a.created_at))
  const pending = pages.error && isMemoryPending(pages.error)
  const ready = pages.pages > 0
  const refresh = () => { pages.reload(); projects.reload() }
  return <section className="memory-catalog" aria-label="记忆条目">
    <div className="processing-heading"><div><h2><BookOpen size={18} />记忆<span className="processing-heading-count">{ready ? `已载入 ${items.length} 条` : '正在读取'}</span></h2><p>保存下来的经验与约定。先看内容，再核对来源和适用范围。</p></div><div className="processing-actions"><Button asChild variant="ghost" size="sm"><a href="#/memory/health">运行状态</a></Button><Button variant="outline" size="sm" disabled={pages.loading} onClick={refresh}><RefreshCw size={14} />刷新</Button></div></div>
    <div className="memory-navigation"><a href="#/memory/items" aria-current="page">已保存的记忆</a><a href="#/interpretations">对话提炼</a><a href="#/reviews/pending">待审核</a><a href="#/memory/jobs">写入队列</a><a href="#/memory/traces">召回记录</a></div>
    {declaredReference ? <p className="memory-reference" data-testid="non-authoritative">记忆仅供历史参考，当前规则和任务状态优先。</p> : null}
    <details className="memory-explainer" open={explain} onToggle={event => setExplain(event.currentTarget.open)}><summary><CircleHelp size={16} /><span>为什么对话很多，记忆却很少？</span><span className="memory-explainer-hint">查看提炼与审核进度</span></summary>{explain ? <MemoryIntake key={project} projectId={project || null} /> : null}</details>
    <div className="rules-toolbar"><label className="rules-search"><Search size={15} /><span className="sr-only">搜索已载入记忆</span><input placeholder="搜索记忆内容或项目" value={search} onChange={e => setSearch(e.target.value)} /></label><select aria-label="记忆所属项目" value={project} onChange={e => setProject(e.target.value)}><option value="">全部项目</option>{projects.items.map(p => <option key={p.project_id} value={p.project_id}>{maskCredentials(p.name).text}</option>)}</select><select aria-label="记忆类型" value={kind} onChange={e => setKind(e.target.value)}><option value="">全部类型</option>{Object.entries(MEMORY_TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select aria-label="记忆状态" value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option><option value="ACTIVE">保留中</option><option value="OBSOLETE">已过期</option><option value="DELETED">已删除</option></select></div>
    {projects.error ? <p className="muted">项目名称暂时无法读取，记忆内容仍保留。<Button size="sm" variant="ghost" onClick={projects.reload}>重试项目名称</Button></p> : null}
    {projects.hasMore ? <Button size="sm" variant="ghost" disabled={projects.loading} onClick={projects.loadMore}>载入更多项目名称</Button> : null}
    {pending ? <MemoryDependencyPending what="记忆列表" method="GET" path="/v1/memory/items" scopes={['memory:read']} /> : pages.error?.kind === 'missing' ? <MemoryNotFound what="记忆列表" /> : pages.error ? <ErrorPanel error={pages.error} what="记忆列表" onRetry={pages.reload} /> : null}
    {!ready && pages.loading ? <Loading what="记忆列表" /> : null}
    {ready && !pages.error && !visible.length ? <div className="rules-empty"><BookOpen size={28} /><h3>{search ? '已载入的记忆中没有匹配内容' : '当前范围还没有记忆'}</h3><p>{search ? '换一个关键词，或继续载入更多记忆。' : '这里是当前凭证可见的空列表。对话需要经过提炼、必要的审核并满足保存条件，才会成为记忆。'}</p><Button asChild size="sm" variant="outline"><a href="#/interpretations">查看对话提炼<ArrowRight size={14} /></a></Button></div> : null}
    {visible.length ? <div className="memory-catalog-table"><table aria-label="记忆内容列表" data-testid="memory-items"><thead><tr><th>记忆内容</th><th>所属项目</th><th>类型</th><th>保存状态</th><th>时间</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{visible.map(item => <tr key={item.memory_id}><td data-label="记忆内容"><a className="rule-content" href={hrefFor({ name: 'memoryItem', memoryId: item.memory_id })}>{memoryPreview(item)}</a><div className="review-row-tags"><Badge variant={item.status === 'ACTIVE' ? 'outline' : 'secondary'}>{memoryState(item)}</Badge>{item.pinned ? <span>已置顶</span> : null}</div></td><td data-label="所属项目">{projectName(item)}{item.task_id ? <span className="rule-secondary">关联项目内任务</span> : null}</td><td data-label="类型">{memoryType(item.memory_type)}</td><td data-label="保存状态"><span className={item.engine_state.includes('FAILED') ? 'memory-operation-failed' : ''}>{memoryEngineState(item)}</span></td><td data-label="时间"><time dateTime={item.created_at} title={formatTimestamp(item.created_at)}>{processingTime(item.created_at)}</time></td><td data-label="操作"><Button asChild size="sm" variant="ghost"><a href={hrefFor({ name: 'memoryItem', memoryId: item.memory_id })}>查看详情</a></Button></td></tr>)}</tbody></table></div> : null}
    {ready ? <div className="review-list-footer"><span>已载入 {items.length} 条{search ? `，匹配 ${visible.length} 条` : ''} · 更新于 {processingTime(pages.loadedAt ?? undefined)}</span>{pages.hasMore ? <Button variant="outline" disabled={pages.loading} onClick={pages.loadMore}>载入更多记忆</Button> : pages.complete ? <span>当前查询范围已读取完毕</span> : null}</div> : null}
    <details className="memory-technical"><summary>功能说明与高级查询</summary><MemorySliceStatus /><p>上方数量为当前凭证、筛选和已载入页面中的条数；不代表整台设备的所有对话或所有记忆。未知类型或状态请在详情核对原始记录。</p><label>按编号打开记忆<input aria-label="按编号打开记忆" placeholder="mem_…" value={lookup} onChange={e => setLookup(e.target.value)} /></label><Button size="sm" variant="outline" disabled={!isCoreId(lookup.trim(), 'mem')} onClick={() => { location.hash = hrefFor({ name: 'memoryItem', memoryId: lookup.trim() }) }}>打开记忆</Button></details>
  </section>
}

/** Counts come from their own read contracts. Applied task changes are never called memories. */
function MemoryIntake({ projectId }: { projectId: string | null }) {
  const { endpoints } = useSession()
  const processing = useResource(signal => endpoints.conversationProcessingSummary(projectId, signal), [projectId])
  const health = useResource(signal => endpoints.memoryHealth(signal), [])
  const counts = processing.data?.counts
  const count = (value: unknown): string => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? String(value) : '未提供'
  return <div className="memory-intake"><p>对话收录 → 内容提炼 → 必要时由你审核 → 满足长期保存条件 → 写入记忆引擎。</p><p>问候、临时问题和创建任务不会自动成为长期记忆。通常需要已批准的约定或决定、验证结果、失败经验等可靠依据。</p>
    <h3>{projectId ? '所选项目的提炼进度' : '全部项目的提炼进度'}</h3>
    {counts ? <><dl className="memory-intake-counts">{[['PENDING', '等待提炼'], ['PROCESSING', '提炼中'], ['PENDING_REVIEW', '待审核'], ['APPLIED', '已应用'], ['NO_ACTION', '无需保存'], ['FAILED', '提炼失败'], ['REJECTED', '已拒绝']].map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{count(counts[key!] ?? 0)}</dd></div>)}</dl><p className="muted">已应用包含创建、关联任务等操作，并不等于已保存为记忆。以上为该范围内的处理记录，不用于和记忆条数直接相减。</p></> : processing.error ? <p>提炼进度暂时不可读取，记忆列表仍可查看。可能需要额外的提炼和来源读取权限。</p> : <p>正在读取提炼进度…</p>}
    {health.data ? <p>本机引擎写入队列（全部可见范围）：等待 {count(health.data.jobs.pending)} · 写入中 {count(health.data.jobs.running)} · 失败 {count(health.data.jobs.failed)}。已完成任务 {count(health.data.jobs.done)} 不等于当前保留的记忆条数。</p> : health.error ? <p>引擎队列统计暂不可读取，不能据此认定没有失败。</p> : null}
    <div className="processing-actions"><Button asChild size="sm" variant="outline"><a href="#/interpretations">查看提炼与失败原因</a></Button><Button asChild size="sm" variant="outline"><a href="#/reviews/pending">处理待审核建议</a></Button><Button asChild size="sm" variant="ghost"><a href="#/memory/jobs">查看写入队列</a></Button><Button size="sm" variant="ghost" onClick={() => { processing.reload(); health.reload() }}>刷新进度</Button></div>
    {(processing.error || health.error) ? <details><summary>读取诊断</summary><p>{processing.error?.code} {health.error?.code}</p></details> : null}
  </div>
}
