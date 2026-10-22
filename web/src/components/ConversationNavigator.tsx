import { useLayoutEffect, useRef, useState } from 'react'
import { Folder, Search } from 'lucide-react'
import type { Project, RawEvent, Session } from '../api/types'
import { agentIdentity } from '../lib/agentIdentity'
import { maskedSummary } from '../lib/text'
import { sourceHref } from '../app/router'

type Origin = 'codex' | 'claude' | 'grok' | 'other' | 'pending'
const GROUPS: { key: Origin; label: string }[] = [
  { key: 'codex', label: 'Codex' }, { key: 'claude', label: 'Claude' }, { key: 'grok', label: 'Grok' },
  { key: 'other', label: 'Core / 其他登记' }, { key: 'pending', label: '来源待确认' },
]
export function projectOrigin(project: Project, registrations: RawEvent[]): Origin {
  const source = registrations.find(event => event.event_id === project.source_event_id)
  if (!source) return 'pending'
  const agent = agentIdentity(source)
  return agent.kind === 'unknown' ? 'other' : agent.kind
}

export function ConversationNavigator({ projects, registrations, sessions, activeProject, loading }: {
  projects: Project[]; registrations: RawEvent[]; sessions: Session[]
  activeProject: string | null; loading: boolean
}) {
  const [search, setSearch] = useState('')
  const [origin, setOrigin] = useState<Origin | 'all'>('all')
  const [compactOpen, setCompactOpen] = useState(false)
  const projectList = useRef<HTMLDivElement>(null)
  const navigation = useRef<HTMLElement>(null)
  const scrolledProject = useRef<string | null>(null)
  useLayoutEffect(() => { scrolledProject.current = null }, [origin, search])
  useLayoutEffect(() => {
    const nav = navigation.current
    if (nav) nav.style.setProperty('--navigation-top', `${Math.round(nav.getBoundingClientRect().top + window.scrollY)}px`)
    const list = projectList.current
    const selected = list?.querySelector('[aria-current="true"]')
    if (!list || !selected || scrolledProject.current === activeProject) return
    const box = list.getBoundingClientRect(), row = selected.getBoundingClientRect()
    const heading = selected.closest('.project-group')?.querySelector('summary')
    const visibleTop = box.top + (heading?.getBoundingClientRect().height ?? 0)
    if (row.top < visibleTop) list.scrollTop += row.top - visibleTop
    else if (row.bottom > box.bottom) list.scrollTop += row.bottom - box.bottom
    scrolledProject.current = activeProject
  })
  const query = search.trim().toLocaleLowerCase()
  const visible = projects.filter(project => (origin === 'all' || projectOrigin(project, registrations) === origin)
    && `${project.name} ${project.description}`.toLocaleLowerCase().includes(query))

  return <aside ref={navigation} className="conversation-navigation" aria-label="项目导航">
    <button type="button" className="conversation-nav-toggle" aria-expanded={compactOpen} onClick={() => setCompactOpen(value => !value)}>
      项目 · {maskedSummary(projects.find(project => project.project_id === activeProject)?.name ?? '选择项目')}
    </button>
    <div className={`navigation-panels${compactOpen ? ' is-open' : ''}`}>
    <section className="panel project-browser" aria-label="按来源浏览项目">
      <header className="panel__head"><h2>项目</h2><span className="muted">已载入 {projects.length} 项</span></header>
      <div className="project-browser__tools">
        <label className="project-search"><Search size={15} aria-hidden="true" /><input aria-label="搜索项目或路径" placeholder="搜索项目或路径" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <details className="project-source-filter"><summary>按来源筛选</summary><select aria-label="项目来源" value={origin} onChange={event => setOrigin(event.target.value as Origin | 'all')}>
          <option value="all">全部来源</option>
          {GROUPS.filter(group => group.key === origin || projects.some(project => projectOrigin(project, registrations) === group.key)).map(group => <option key={group.key} value={group.key}>{group.label}</option>)}
        </select></details>
      </div>
      <div ref={projectList} className="project-browser__list">
        {GROUPS.map(group => {
          const rows = visible.filter(project => projectOrigin(project, registrations) === group.key)
          if (rows.length === 0) return null
          return <details className="project-group" key={group.key} open={group.key !== 'other' || rows.some(project => project.project_id === activeProject) || query.length > 0 || origin !== 'all'}>
            <summary>{group.label}<span>{rows.length}</span></summary>
            <ul>{[...rows].reverse().map(project => {
              const count = sessions.filter(session => session.project_id === project.project_id).length
              return <li key={project.project_id}><a className="project-choice" title={maskedSummary(project.description)} href={sourceHref.conversation(project.project_id)} onClick={() => setCompactOpen(false)} aria-current={project.project_id === activeProject ? 'true' : undefined}>
                <Folder size={16} aria-hidden="true" /><span><strong title={maskedSummary(project.name)}>{maskedSummary(project.name)}</strong>
                  <em>{count > 0 ? `${count} 个会话` : '暂无会话'}</em></span>
              </a></li>
            })}</ul>
          </details>
        })}
        {!loading && visible.length === 0 ? <p className="muted project-no-results">没有匹配的已登记项目</p> : null}
      </div>
    </section>
    </div>
  </aside>
}
