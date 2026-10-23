import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight, Folder, MessageSquare, Search } from 'lucide-react'
import type { Project, RawEvent, Session } from '../api/types'
import type { DetectedAgent } from '../app/useAgentDiscovery'
import type { SessionPreview } from '../app/useSessionPreviews'
import { captureLabel } from './AgentCaptureAction'
import { AgentLogo } from './AgentLogo'
import { groupSessionsByTime } from '../lib/conversationTime'
import { agentIdentity } from '../lib/agentIdentity'
import { formatTimestamp, maskedSummary } from '../lib/text'
import { sourceHref } from '../app/router'

type Origin = 'codex' | 'claude' | 'grok' | 'other' | 'pending'
const GROUPS = [
  { key: 'codex', label: 'Codex' }, { key: 'claude', label: 'Claude' }, { key: 'grok', label: 'Grok' },
  { key: 'other', label: 'Core / 其他登记' }, { key: 'pending', label: '来源待确认' },
]
export function projectOrigin(project: Project, registrations: RawEvent[]): Origin {
  const source = registrations.find(event => event.event_id === project.source_event_id)
  if (!source) return 'pending'
  const agent = agentIdentity(source)
  return agent.kind === 'unknown' ? 'other' : agent.kind
}

export function ConversationNavigator({ projects, registrations, sessions, activeProject, activeSession, previews, agents, discoveryStatus, expandedProjects, onToggleProject, loading, mode, onModeChange, search, onSearchChange, timelineSessions, timelineTotal, onMoreTimeline }: {
  mode: 'agent' | 'time'; onModeChange: (mode: 'agent' | 'time') => void; search: string; onSearchChange: (value: string) => void
  timelineSessions: Session[]; timelineTotal: number; onMoreTimeline: () => void
  projects: Project[]; registrations: RawEvent[]; sessions: Session[]
  activeProject: string | null; activeSession: string | null; previews: Record<string, SessionPreview>
  agents: DetectedAgent[]; discoveryStatus: string; expandedProjects: string[]; onToggleProject: (id: string) => void; loading: boolean
}) {
  const [compactOpen, setCompactOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState<Record<string, boolean>>({})
  const projectList = useRef<HTMLDivElement>(null)
  const navigation = useRef<HTMLElement>(null)
  const scrolledSelection = useRef('')
  const activeOrigin = projects.find(project => project.project_id === activeProject)
  const currentOrigin = activeOrigin ? projectOrigin(activeOrigin, registrations) : null
  useEffect(() => { if (currentOrigin) setAgentOpen(previous => ({ ...previous, [currentOrigin]: true })) }, [currentOrigin, activeProject, activeSession])
  useLayoutEffect(() => {
    const list = projectList.current
    if (mode === 'time' && list) {
      // A grouping/search change opens at newest; keep the conversation reader.
      list.scrollTop = 0
      const selected = list.querySelector('.session-choice[aria-current="true"]')
      scrolledSelection.current = JSON.stringify([activeProject, activeSession, selected?.getAttribute('href')])
    } else scrolledSelection.current = ''
  }, [search, compactOpen, mode])
  useLayoutEffect(() => {
    const nav = navigation.current
    if (nav) nav.style.setProperty('--navigation-top', `${Math.round(nav.getBoundingClientRect().top + window.scrollY)}px`)
    const list = projectList.current
    const selected = list?.querySelector('.session-choice[aria-current="true"]') ?? list?.querySelector('.project-choice[aria-current="true"]')
    const key = JSON.stringify([activeProject, activeSession, selected?.getAttribute('href')])
    if (!list || !selected || scrolledSelection.current === key || selected.getClientRects().length === 0) return
    const box = list.getBoundingClientRect(), row = selected.getBoundingClientRect()
    if (row.top < box.top) list.scrollTop += row.top - box.top
    else if (row.bottom > box.bottom) list.scrollTop += row.bottom - box.bottom
    scrolledSelection.current = key
  })
  const query = search.trim().toLocaleLowerCase()
  const groups = [...GROUPS]
  for (const agent of agents.filter(agent => agent.detected)) {
    if (!groups.some(group => group.key === agent.id)) groups.splice(Math.max(0, groups.length - 2), 0, { key: agent.id, label: agent.name })
  }
  const visible = groups.map(group => {
    const installed = agents.find(agent => agent.id === group.key && agent.detected)
    const recorded = projects.filter(project => projectOrigin(project, registrations) === group.key)
    const rows = recorded.filter(project => `${project.name} ${project.description} ${group.label}`.toLocaleLowerCase().includes(query))
    return { ...group, installed, recorded, rows }
  }).filter(group => (group.recorded.length > 0 || group.installed) && (!query || group.rows.length > 0 || group.label.toLocaleLowerCase().includes(query)))

  return <aside ref={navigation} className="conversation-navigation" aria-label="项目导航">
    <button type="button" className="conversation-nav-toggle" aria-expanded={compactOpen} onClick={() => setCompactOpen(value => !value)}>
      项目 · {maskedSummary(projects.find(project => project.project_id === activeProject)?.name ?? '选择项目')}
    </button>
    <div className={`navigation-panels${compactOpen ? ' is-open' : ''}`}>
      <section className="panel project-browser" aria-label="Agent、项目与会话导航">
        <header className="panel__head"><h2>项目与会话</h2><span className="muted">{mode === 'agent' ? `${projects.length} 个项目` : `${timelineTotal} 个会话`}</span></header>
        <div className="project-browser__tools">
          <div className="conversation-view-switch" role="group" aria-label="浏览方式">
            <button type="button" aria-pressed={mode === 'agent'} onClick={() => onModeChange('agent')}>按 Agent</button>
            <button type="button" aria-pressed={mode === 'time'} onClick={() => onModeChange('time')}>按时间</button>
          </div>
          <label className="project-search"><Search size={15} aria-hidden="true" /><input aria-label="搜索项目或路径" placeholder="搜索项目或路径" value={search} onChange={event => onSearchChange(event.target.value)} /></label>
        </div>
        <div ref={projectList} className="project-browser__list">
          {mode === 'agent' ? <ul className="conversation-tree" aria-label="按 Agent 浏览">
            {visible.map(group => {
              const open = query.length > 0 || (agentOpen[group.key] ?? group.key === currentOrigin)
              return <li key={group.key}><details className="project-group agent-tree-node" open={open}>
                <summary onClick={event => { event.preventDefault(); setAgentOpen(previous => ({ ...previous, [group.key]: !open })) }}>
                  <ChevronRight size={14} className="tree-chevron" aria-hidden="true" /><AgentLogo agent={group.key} size={17} />
                  <strong>{group.label}</strong><span>{group.recorded.length > 0 ? `${group.recorded.length} 项目` : group.installed?.capture?.configured ? '已启用 · 等待消息' : '已安装 · 待接入'}</span>
                </summary>
                {group.rows.length > 0 ? <ul className="tree-children">{[...group.rows].reverse().map(project => {
                  const children = sessions.filter(session => session.project_id === project.project_id)
                    .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.session_id.localeCompare(a.session_id))
                  const expanded = expandedProjects.includes(project.project_id)
                  return <li className="project-node" key={project.project_id}>
                    <div className="project-tree-row">
                      <button type="button" className="tree-expand" aria-label={`${expanded ? '折叠' : '展开'}项目 ${maskedSummary(project.name)}`} aria-expanded={expanded} onClick={() => onToggleProject(project.project_id)}>
                        <ChevronRight size={13} aria-hidden="true" />
                      </button>
                      <a className="project-choice" title={maskedSummary(project.description)} href={sourceHref.conversation(project.project_id)} onClick={() => setCompactOpen(false)} aria-current={project.project_id === activeProject ? 'true' : undefined}>
                        <Folder size={15} aria-hidden="true" /><strong>{maskedSummary(project.name)}</strong><span>{children.length}</span>
                      </a>
                    </div>
                    {expanded ? <ul className="tree-children session-tree">{children.map(session => <li key={session.session_id}>
                      <a className="session-choice" href={sourceHref.conversation(session.project_id, session.task_id, session.session_id)} onClick={() => setCompactOpen(false)} aria-current={session.session_id === activeSession ? 'true' : undefined}>
                        <MessageSquare size={13} aria-hidden="true" /><span><strong>{previews[session.session_id]?.title ?? '正在读取摘要…'}</strong>
                          <small><time dateTime={session.started_at} title={formatTimestamp(session.started_at)}>{new Date(session.started_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}</time></small>
                        </span>
                      </a>
                    </li>)}{children.length === 0 ? <li className="tree-note">尚无会话记录</li> : null}</ul> : null}
                  </li>
                })}</ul> : <p className="tree-note">{group.installed?.capture?.configured ? '已启用采集。新开会话发送消息后，会自动出现在这里。' : group.installed && !group.installed.capture_supported ? '已检测到客户端，尚未接入采集。' : group.installed ? `${captureLabel(group.installed)}，请在右上角 Agent 接入中启用。` : '尚无项目或会话记录。'}</p>}
              </details></li>
            })}
          </ul> : <div className="conversation-timeline" aria-label="按会话开始时间浏览">
            <p className="timeline-basis">按会话开始时间 · 本地时区</p>
            {groupSessionsByTime(timelineSessions).map(group => <section className="timeline-group" key={group.key}>
              <h3>{group.label}<span>{group.sessions.length}</span></h3>
              <ul>{group.sessions.map(session => {
                const project = projects.find(project => project.project_id === session.project_id)
                const preview = previews[session.session_id]
                const origin = preview?.agent?.toLocaleLowerCase() ?? (project ? projectOrigin(project, registrations) : 'pending')
                const date = new Date(session.started_at)
                return <li key={session.session_id}><a className="session-choice timeline-choice"
                  href={sourceHref.conversation(session.project_id, session.task_id, session.session_id)}
                  onClick={() => setCompactOpen(false)} aria-current={session.session_id === activeSession ? 'true' : undefined}>
                  <AgentLogo agent={origin} size={17} /><span><strong>{preview?.title ?? '正在读取摘要…'}</strong>
                    <small title={maskedSummary(project?.description ?? '')}>{maskedSummary(project?.name ?? '未关联项目')}</small>
                    <time dateTime={session.started_at} title={formatTimestamp(session.started_at)}>{Number.isNaN(date.getTime()) ? '时间未记录' : date.toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false })}</time>
                  </span>
                </a></li>
              })}</ul>
            </section>)}
            {!loading && timelineSessions.length === 0 ? <p className="tree-note">没有匹配的会话记录</p> : null}
            {timelineSessions.length < timelineTotal ? <button className="timeline-more" type="button" onClick={onMoreTimeline}>继续加载会话（已显示 {timelineSessions.length} / {timelineTotal}）</button> : null}
          </div>}
          {mode === 'agent' && !loading && visible.length === 0 ? <p className="muted project-no-results">没有匹配的项目或 Agent</p> : null}
          {discoveryStatus ? <p className="tree-note">{discoveryStatus}</p> : null}
        </div>
      </section>
    </div>
  </aside>
}
