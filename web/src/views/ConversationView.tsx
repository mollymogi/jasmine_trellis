/** Conversation browser: recorded source -> workspace -> one native session.
 * Registry reads never imply a scan of the machine or capture for every Agent.
 */
import { useEffect, useId, useState } from 'react'
import { DEFAULT_OBJECT_LIMIT } from '../api/endpoints'
import type { Project, Session } from '../api/types'
import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { offsetLoader, usePagedResource } from '../app/usePagedResource'
import { useLiveObjects } from '../app/useLiveObjects'
import { useConversationEvents } from '../app/useConversationEvents'
import { useSessionPreviews } from '../app/useSessionPreviews'
import { isConversationMessage } from '../components/ConversationMessage'
import { ConversationThread } from '../components/ConversationThread'
import { ConversationNavigator } from '../components/ConversationNavigator'
import { Pager } from '../components/Pager'
import { Empty, ErrorPanel, Loading, Panel } from '../components/States'
import { AgentConnections } from '../components/AgentConnections'
import { useAgentDiscovery } from '../app/useAgentDiscovery'
import { sortSessionsByStart } from '../lib/conversationTime'
import { maskedSummary } from '../lib/text'
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


export function ConversationView({ projectId, taskId, sessionId }: {
  projectId: string | null; taskId: string | null; sessionId: string | null
}): JSX.Element {
  const { endpoints } = useSession()
  const discovery = useAgentDiscovery()
  const [expandedProjects, setExpandedProjects] = useState<string[]>([])
  const [navigationMode, setNavigationMode] = useState<'agent' | 'time'>('agent')
  const [navigationSearch, setNavigationSearch] = useState('')
  const [timelineLimit, setTimelineLimit] = useState(40)
  const [eventType, setEventType] = useState('')
  const [sourceSystem, setSourceSystem] = useState('')
  const eventTypeId = useId()
  const sourceId = useId()
  const projects = useLiveObjects<Project>(async (offset, limit, signal) => {
    const page = await endpoints.listProjects({ limit, offset }, signal)
    return { items: page.projects, count: page.count }
  }, null)
  const sessions = useLiveObjects<Session>(async (offset, limit, signal) => {
    const page = await endpoints.listSessions(null, { limit, offset }, signal)
    return { items: page.sessions, count: page.count }
  }, null)
  const registrations = useConversationEvents(endpoints, { event_type: 'project.created' })
  const [initialProject, setInitialProject] = useState<string | null>(null)
  const requestedSession = sessions.items.find(session => session.session_id === sessionId)
  useEffect(() => {
    if (projectId || sessionId || initialProject || projects.loading || sessions.loading) return
    const recent = [...sessions.items].sort((a, b) => b.started_at.localeCompare(a.started_at))
      .find(session => projects.items.some(project => project.project_id === session.project_id))
    setInitialProject(recent?.project_id ?? projects.items.at(-1)?.project_id ?? null)
  }, [projectId, sessionId, initialProject, projects.items, projects.loading, sessions.items, sessions.loading])
  const activeProject = projectId ?? requestedSession?.project_id ?? initialProject
  const selectedProject = projects.items.find(project => project.project_id === activeProject)
  const scope = JSON.stringify([activeProject, taskId])
  const [defaultSession, setDefaultSession] = useState<{ scope: string; id: string | null }>({ scope: '', id: null })
  const projectSessions = sessions.items.filter(session => session.project_id === activeProject && (!taskId || session.task_id === taskId))
    .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.session_id.localeCompare(a.session_id))
  const timeMatches = sortSessionsByStart(sessions.items.filter(session => {
    const project = projects.items.find(project => project.project_id === session.project_id)
    return `${project?.name ?? '未关联项目'} ${project?.description ?? ''}`.toLocaleLowerCase().includes(navigationSearch.trim().toLocaleLowerCase())
  }))
  const timelineSessions = timeMatches.slice(0, timelineLimit)
  const previewSessions = navigationMode === 'time' ? [...projectSessions, ...timelineSessions]
    : sessions.items.filter(session => session.project_id === activeProject || (session.project_id !== null && expandedProjects.includes(session.project_id)))
  const previews = useSessionPreviews(endpoints, [...new Set(previewSessions.map(session => session.session_id))])
  useEffect(() => {
    if (sessionId || !activeProject || sessions.loading) return
    if (defaultSession.scope === scope && defaultSession.id !== null) return
    const rows = [...sessions.items].filter(session => session.project_id === activeProject && (!taskId || session.task_id === taskId))
      .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.session_id.localeCompare(a.session_id))
    const readyIndex = rows.findIndex(session => previews[session.session_id]?.state === 'ready')
    // Prefer a conversation that already has user content over a newer empty
    // SessionStart. Wait for newer summaries before choosing once; never move
    // an explicit selection when a new conversation later arrives.
    if ((readyIndex < 0 ? rows : rows.slice(0, readyIndex)).some(session => !previews[session.session_id])) return
    const recent = readyIndex >= 0 ? rows[readyIndex] : rows[0]
    if (defaultSession.scope !== scope || recent) setDefaultSession({ scope, id: recent?.session_id ?? null })
  }, [sessionId, activeProject, taskId, scope, sessions.items, sessions.loading, defaultSession, previews])
  const activeSession = sessionId ?? (defaultSession.scope === scope ? defaultSession.id : null)
  useEffect(() => {
    if (activeProject) setExpandedProjects(previous => previous.includes(activeProject) ? previous : [...previous, activeProject])
  }, [activeProject, activeSession])
  const selectedSession = sessions.items.find(session => session.session_id === activeSession)
  const validSession = !!selectedSession && selectedSession.project_id === activeProject && (!taskId || selectedSession.task_id === taskId)
  const events = useConversationEvents(endpoints, {
    session_id: activeSession, event_type: eventType || null, source_system: sourceSystem || null,
  }, validSession)
  const messages = events.items.filter(isConversationMessage)
  const systemEvents = events.items.filter(event => !isConversationMessage(event))
  const tasks = usePagedResource(activeProject ? offsetLoader<{ task_id: string; title: string }>({
    limit: DEFAULT_OBJECT_LIMIT,
    fetch: async (offset, limit, signal) => {
      const page = await endpoints.listTasks(activeProject, { limit, offset }, signal)
      return { items: page.tasks.map(task => ({ task_id: task.task_id, title: task.title })), count: page.count }
    },
  }) : null, [activeProject])

  return <div className="stack conversation-page">
    <section className="conversation-context" aria-label="当前会话范围">
      <div><h1>{selectedProject ? maskedSummary(selectedProject.name) : '项目对话'}</h1>
      </div>
      <AgentConnections discovery={discovery} />
    </section>
    {projects.error ? <ErrorPanel error={projects.error} what="项目列表" onRetry={projects.reload} /> : null}
    {sessions.error ? <ErrorPanel error={sessions.error} what="会话列表" onRetry={sessions.reload} /> : null}
    {registrations.error ? <p className="state state--meta">项目来源暂未读取，未确认的项目保留在“来源待确认”中。</p> : null}
    <div className="conversation-browser">
      <ConversationNavigator projects={projects.items} registrations={registrations.items} sessions={sessions.items}
        activeProject={activeProject} activeSession={validSession ? activeSession : null} previews={previews}
        mode={navigationMode} onModeChange={setNavigationMode} search={navigationSearch}
        onSearchChange={value => { setNavigationSearch(value); setTimelineLimit(40) }} timelineSessions={timelineSessions}
        timelineTotal={timeMatches.length} onMoreTimeline={() => setTimelineLimit(value => value + 40)}
        agents={discovery.agents} discoveryStatus={discovery.status} expandedProjects={expandedProjects}
        onToggleProject={id => setExpandedProjects(previous => previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id])}
        loading={projects.loading || sessions.loading} />
      <div className="stack conversation-reader">
        {validSession ? <ConversationThread key={JSON.stringify([activeProject, activeSession, eventType, sourceSystem])}
          messages={messages} systemEvents={systemEvents} taskId={selectedSession?.task_id ?? taskId}
          loading={events.loading} error={events.error} loadedAt={events.loadedAt} onReload={events.reload} />
          : <Panel title="对话">{sessions.loading || !sessionId && projectSessions.length > 0 ? <Loading what="会话" />
            : sessionId ? <p className="state state--error">该会话不在当前项目或任务范围内，或尚未读取到对应会话。不会合并展示其他会话。</p>
            : <Empty what="会话" hint={activeProject ? '此项目尚未采集到会话。' : '请从左侧选择一个已登记项目。'} />}</Panel>}
        <details className="conversation-filters"><summary>筛选与来源详情</summary>
          <Panel title="读取范围"><div className="filters">
            <label htmlFor={eventTypeId}>event_type<select id={eventTypeId} value={eventType} onChange={event => setEventType(event.target.value)}>
              <option value="">(全部)</option>{EVENT_TYPES.map(candidate => <option key={candidate} value={candidate}>{candidate}</option>)}
            </select></label>
            <label htmlFor={sourceId}>source_system<input id={sourceId} value={sourceSystem} onChange={event => setSourceSystem(event.target.value)} placeholder="例如 codex" autoComplete="off" /></label>
          </div><p className="state state--meta">当前只读取所选会话。时间按本地时区 {Intl.DateTimeFormat().resolvedOptions().timeZone} 显示，每 3 秒自动同步。</p>
          <dl className="conversation-identifiers"><dt>工作目录 / 描述</dt><dd>{maskedSummary(selectedProject?.description ?? '尚未读取')}</dd>
            <dt>项目编号</dt><dd>{activeProject ?? '未选择'}</dd><dt>会话编号</dt><dd>{activeSession ?? '未选择'}</dd></dl>
          </Panel>
        </details>
        <details className="conversation-explanation"><summary>任务、解释与运行记录</summary>
          <Panel title="当前项目任务"><ul className="plain">{tasks.items.map(task => <li key={task.task_id}><a href={sourceHref.task(task.task_id)}>{task.title}</a></li>)}</ul>
            <Pager paged={tasks} noun="Task" limit={DEFAULT_OBJECT_LIMIT} />
          </Panel>
          <Panel title="Interpretation / Resolver(读取视图已交付)">
            <p><a href={sourceHref.interpretations(activeProject, taskId)}>当前范围的全部解释</a>{' · '}<a href={sourceHref.reviews(activeProject, taskId)}>当前范围的待审批 Review</a></p>
            <p className="muted">写入操作位于解释详情页与 Review 详情页，本页只读 Raw Event，不根据消息内容推断审批结果。</p>
          </Panel><HookDecisions />
        </details>
      </div>
    </div>
  </div>
}
