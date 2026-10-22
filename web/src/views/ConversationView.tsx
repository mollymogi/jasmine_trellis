/**
 * Conversation / Event view (P6-A05).
 *
 * Events come from `GET /v1/events` with Core-side filters (`session_id`,
 * `task_id`, `project_id`, `event_type`, `source_system`) and `after_seq` paging.
 * There is no task-timeline endpoint in the baseline, so the panel states its
 * exact read scope and read time instead of implying a transactional snapshot or
 * complete chat capture.
 *
 * Tool results and other machine events keep their own label and can never be
 * rendered as a human utterance. The Interpretation column links to the delivered
 * read views instead of guessing: P2 owns that contract, and the dashboard shows
 * what Core recorded rather than inferring it from event text.
 */

import { useId, useMemo, useRef, useState } from 'react'

import { DEFAULT_OBJECT_LIMIT } from '../api/endpoints'
import type { Project, Session } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { offsetLoader, usePagedResource } from '../app/usePagedResource'
import { EventRow } from '../components/EventRow'
import { ConversationMessage, isConversationMessage } from '../components/ConversationMessage'
import { useConversationEvents } from '../app/useConversationEvents'
import { useLiveObjects } from '../app/useLiveObjects'
import { Pager } from '../components/Pager'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel } from '../components/States'
import { formatTimestamp } from '../lib/text'
import { HookDecisions } from '../components/HookDecisions'

const EVENT_TYPES = [
  'user.prompt',
  'human.confirmation_prepared',
  'human.confirmation_submitted',
  'assistant.message',
  'session.started',
  'session.ended',
  'project.created',
  'task.created',
  'project.notes',
  'rule.proposed',
  'rule.approved',
  'rule.superseded',
  'rule.retired',
  'step.created',
  'step.transitioned',
  'step.criteria_updated',
  'task.transitioned',
  'task.criteria_updated',
  'task.accepted',
  'tool.call',
  'tool.result',
  'evidence.recorded',
  'evidence.confirmed',
  'workspace.fingerprinted',
  'step.staled',
  // P2 additions. This list must match `models.EVENT_TYPES` in the frozen Core;
  // tests/p6/test_dashboard_proxy_contract.py parses it and fails on drift.
  'interpretation.bound',
  'interpretation.corrected',
  'interpretation.rejected',
  'interpretation.rerun',
  'resolution.command',
  'review.approved',
  'review.rejected',
] as const

export function ConversationView({
  projectId,
  taskId,
  sessionId,
}: {
  projectId: string | null
  taskId: string | null
  sessionId: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const [eventType, setEventType] = useState('')
  const [sourceSystem, setSourceSystem] = useState('')
  const eventTypeId = useId()
  const sourceId = useId()
  const latestMessage = useRef<HTMLDivElement>(null)

  const projects = useLiveObjects<Project>(async (offset, limit, signal) => {
    const page = await endpoints.listProjects({ limit, offset }, signal)
    return { items: page.projects, count: page.count }
  }, null)
  const sessions = useLiveObjects<Session>(async (offset, limit, signal) => {
    const page = await endpoints.listSessions(projectId, { limit, offset }, signal)
    return { items: page.sessions, count: page.count }
  }, projectId)

  const tasks = usePagedResource(
    offsetLoader<{ task_id: string; title: string }>({
      limit: DEFAULT_OBJECT_LIMIT,
      fetch: async (offset, limit, signal) => {
        const page = await endpoints.listTasks(projectId, { limit, offset }, signal)
        return {
          items: page.tasks.map((task) => ({ task_id: task.task_id, title: task.title })),
          count: page.count,
        }
      },
    }),
    [projectId],
  )

  const events = useConversationEvents(endpoints, {
    session_id: sessionId,
    task_id: sessionId === null ? taskId : null,
    project_id: sessionId === null && taskId === null ? projectId : null,
    event_type: eventType || null,
    source_system: sourceSystem || null,
  })
  const messages = events.items.filter(isConversationMessage)
  const systemEvents = events.items.filter(event => !isConversationMessage(event))
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone

  const scopeLabel = useMemo(() => {
    if (sessionId) return `session ${sessionId}`
    if (taskId) return `task ${taskId}`
    if (projectId) return `project ${projectId}`
    return '全部 Event(无过滤)'
  }, [projectId, taskId, sessionId])

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Conversation</span>
      </nav>

      <Panel title="项目">
        <div className="filters">
          <label>查看项目
            <select aria-label="查看项目" value={projectId ?? ''} onChange={event => {
              window.location.hash = sourceHref.conversation(event.target.value || null)
            }}>
              <option value="">全部项目</option>
              {[...projects.items].reverse().map(project => <option key={project.project_id} value={project.project_id}>
                {project.name}{project.description ? ` · ${project.description}` : ''}
              </option>)}
            </select>
          </label>
          <span className="muted">项目、会话和消息每 3 秒自动更新</span>
        </div>
        {projects.error ? <ErrorPanel error={projects.error} what="项目" onRetry={projects.reload} /> : null}
      </Panel>

      <Panel title="读取范围">
        <div className="filters">
          <label htmlFor={eventTypeId}>
            event_type
            <select
              id={eventTypeId}
              value={eventType}
              onChange={(event) => setEventType(event.target.value)}
            >
              <option value="">(全部)</option>
              {EVENT_TYPES.map((candidate) => (
                <option key={candidate} value={candidate}>
                  {candidate}
                </option>
              ))}
            </select>
          </label>
          <label htmlFor={sourceId}>
            source_system
            <input
              id={sourceId}
              value={sourceSystem}
              onChange={(event) => setSourceSystem(event.target.value)}
              placeholder="例如 codex"
              autoComplete="off"
            />
          </label>
        </div>
        <p className="state state--meta">
          当前范围：<strong>{scopeLabel}</strong>。时间按本地时区 {localZone} 显示。
          对话从上到下按发生顺序排列，每 3 秒自动刷新。
        </p>
        <div className="scope-links">
          <a href={sourceHref.conversation(projectId)} aria-current={sessionId === null && taskId === null ? 'true' : undefined}>
            整个 Project
          </a>
          {taskId ? (
            <a href={sourceHref.conversation(projectId, taskId)} aria-current={sessionId === null ? 'true' : undefined}>
              当前 Task
            </a>
          ) : null}
        </div>
      </Panel>

      <div className="split">
        <Panel
          title={`对话（${messages.length} 条消息）`}
          actions={
            <>
              <button type="button" onClick={() => latestMessage.current?.scrollIntoView({ block: 'center' })} disabled={messages.length === 0}>
                跳到最新消息
              </button>
              <button type="button" onClick={events.reload} disabled={events.loading}>重新读取</button>
            </>
          }
        >
          {events.loading && events.items.length === 0 ? <Loading what="Event" /> : null}
          {events.error ? (
            <ErrorPanel error={events.error} what="Event" onRetry={events.reload} />
          ) : null}
          {!events.loading && events.error === null && events.items.length === 0 ? (
            <Empty
              what="Event"
              hint="本地采集没有这些事件;本页绝不凭 UI 状态或本地对话补造。"
            />
          ) : null}
          {events.items.length > 0 && messages.length === 0 ? <p className="muted">此范围暂无用户或助手消息。</p> : null}
          <ol className="chat-messages" aria-label="用户和助手对话">
            {messages.map(event => <ConversationMessage key={event.event_id} event={event} taskId={taskId} />)}
          </ol>
          <div ref={latestMessage} />
          <p className="state state--meta" role="status">
            {events.error ? '自动刷新失败，保留上次内容；将自动重试。' : events.loading ? '正在同步对话…' : '每 3 秒自动刷新'}
            {events.loadedAt ? <> · 上次同步 <time dateTime={events.loadedAt}>{formatTimestamp(events.loadedAt)}</time></> : null}
          </p>
          <p className="muted">助手回复完成后显示；生成中的文字和过程更新尚未采集。</p>
          <details className="system-events">
            <summary>系统事件（{systemEvents.length} 条，默认折叠）</summary>
            <ol className="events">
              {systemEvents.map(event => <EventRow key={event.event_id} event={event} taskId={taskId} />)}
            </ol>
          </details>
        </Panel>

        <div className="stack">
          <Panel title="Interpretation / Resolver(读取视图已交付)">
            <p className="state state--meta">
              左列展示已采集的用户和助手消息，系统事件与来源详情可展开查看；解释、候选、Resolver 计划与待审批 Review 现在有独立的只读视图。
              P2 契约已合并,browser 只读这几条 GET:
            </p>
            <ul className="plain">
              <li>
                <a href={sourceHref.interpretations(projectId, taskId)}>当前范围的全部解释</a>
                {' · '}
                <a href={sourceHref.interpretations(projectId, taskId, null, 'EXTRACTED')}>
                  仅 EXTRACTED
                </a>
                {' · '}
                <a href={sourceHref.interpretations(projectId, taskId, null, 'PROCESSING')}>
                  处理中
                </a>
              </li>
              <li>
                <a href={sourceHref.reviews(projectId, taskId)}>当前范围的待审批 Review</a>
              </li>
            </ul>
            <p className="state state--meta">
              本页不会用关键词或本地推断顶替解释结果;<strong>写入</strong>(correct/reject/rerun、
              approve/reject)在<strong>解释详情页</strong>与<strong>Review 详情页</strong>交付,
              本页只读 Raw Event,不提供写按钮。
            </p>
          </Panel>

          <Panel title="Sessions">
            {sessions.items.length === 0 && !sessions.loading ? <Empty what="Session" /> : null}
            <ul className="plain">
              {[...sessions.items].reverse().map((session) => (
                <li key={session.session_id}>
                  <a
                    href={sourceHref.conversation(session.project_id, session.task_id, session.session_id)}
                    aria-current={session.session_id === sessionId ? 'true' : undefined}
                  >
                    <code>{session.session_id}</code>
                  </a>{' '}
                  <StatusBadge kind="session" status={session.status} />{' '}
                  <span className="muted">{formatTimestamp(session.started_at)}</span>
                  {' · '}{projects.items.find(project => project.project_id === session.project_id)?.name ?? session.project_id}
                </li>
              ))}
            </ul>
            {sessions.loading ? <Loading what="Session" /> : null}
            {sessions.error ? <ErrorPanel error={sessions.error} what="Session" onRetry={sessions.reload} /> : null}
          </Panel>

          <Panel title="Tasks">
            {tasks.items.length === 0 && !tasks.loading ? <Empty what="Task" /> : null}
            <ul className="plain">
              {tasks.items.map((task) => (
                <li key={task.task_id}>
                  <a
                    href={sourceHref.conversation(projectId, task.task_id)}
                    aria-current={task.task_id === taskId ? 'true' : undefined}
                  >
                    {task.title}
                  </a>
                </li>
              ))}
            </ul>
            <Pager paged={tasks} noun="Task" limit={DEFAULT_OBJECT_LIMIT} />
          </Panel>
        </div>
      </div>
      <HookDecisions />
    </div>
  )
}
