import { useState } from 'react'
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { Project, RawEvent, Session } from '../src/api/types'
import { CoreClient } from '../src/api/client'
import { makeEndpoints } from '../src/api/endpoints'
import { SessionProvider } from '../src/app/session'
import { useConversationEvents } from '../src/app/useConversationEvents'
import { useSessionPreviews } from '../src/app/useSessionPreviews'
import { ConversationView } from '../src/views/ConversationView'
import { ConversationNavigator } from '../src/components/ConversationNavigator'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | undefined
afterEach(() => { fake?.restore(); vi.restoreAllMocks(); vi.useRealTimers() })
const time = '2026-10-21T15:00:00Z'
const project = (id: string, name: string): Project => ({ project_id: id, name, description: `/work/${name}`,
  source_event_id: `evt_${id}`, status: 'active', revision: 1, created_at: time, updated_at: time })
const session = (id: string, projectId: string, minute: number): Session => ({ session_id: id, project_id: projectId,
  task_id: null, actor_id: 'act_1', host_id: 'hst_1', status: 'open', revision: 1, source_event_id: `evt_${id}`,
  started_at: `2026-10-21T15:0${minute}:00Z`, ended_at: null, created_at: time, updated_at: time })
function event(seq: number, type: string, text: string, sessionId: string | null, projectId = 'prj_A'): RawEvent {
  return { seq, event_id: `evt_${seq}`, event_type: type, actor_kind: type === 'user.prompt' ? 'human' : 'system',
    schema_version: 13, source_system: 'codex-native-conversation-v1', source_event_id: null,
    occurred_at: time, recorded_at: time, actor_id: 'act_1', host_id: 'hst_1', session_id: sessionId,
    project_id: projectId, task_id: null, payload: { text }, body_sha256: 'a'.repeat(64) }
}
const flush = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }

it('shows installed agents and nests independent sessions beneath projects without a source selector', () => {
  const a = project('prj_A', 'Claude-named-folder'), b = project('prj_B', 'Operations')
  const registered = event(1, 'project.created', '', null);registered.event_id = a.source_event_id
  const manual = event(2, 'project.created', '', null);manual.event_id = b.source_event_id;manual.source_system = 'core-api'
  function Tree() {
    const [expanded, setExpanded] = useState(['prj_A'])
    const [mode, setMode] = useState<'agent' | 'time'>('agent')
    const [search, setSearch] = useState('')
    return <ConversationNavigator projects={[a,b]} registrations={[registered,manual]} sessions={[session('ses_A','prj_A',1)]}
      mode={mode} onModeChange={setMode} search={search} onSearchChange={setSearch} timelineSessions={[session('ses_A','prj_A',1)]} timelineTotal={1} onMoreTimeline={()=>{}}
      activeProject="prj_A" activeSession="ses_A" loading={false} previews={{ses_A:{title:'整理开发文档',state:'ready',agent:'Codex'}}}
      agents={[{id:'claude',name:'Claude',cli:true,desktop:true,detected:true,capture_supported:false},{id:'opencode',name:'OpenCode',cli:true,desktop:false,detected:true,capture_supported:false}]}
      discoveryStatus="" expandedProjects={expanded} onToggleProject={id=>setExpanded(rows=>rows.includes(id)?rows.filter(row=>row!==id):[...rows,id])} />
  }
  render(<Tree />)
  expect(screen.getByText('Claude-named-folder').closest('.project-group')?.querySelector('summary')?.textContent).toBe('Codex1 项目')
  expect(screen.getByText('Operations').closest('.project-group')?.querySelector('summary')?.textContent).toContain('Core / 其他登记')
  expect(screen.getByText('OpenCode').closest('summary')?.textContent).toContain('已安装 · 待接入')
  for (const name of ['Codex','Claude','OpenCode']) expect(screen.getByText(name,{exact:true}).closest('summary')?.querySelector('[data-agent-logo]')?.getAttribute('data-agent-logo')).toBe(name.toLowerCase())
  fireEvent.click(screen.getByText('Claude',{exact:true}))
  expect(screen.getByText('Claude',{exact:true}).closest('details')?.open).toBe(true)
  expect(screen.getAllByText('已检测到客户端，尚未接入采集。').length).toBe(2)
  expect(screen.getByRole('link',{name:/整理开发文档/}).closest('.project-node')?.textContent).toContain('Claude-named-folder')
  fireEvent.click(screen.getByRole('button',{name:'折叠项目 Claude-named-folder'}))
  expect(screen.queryByRole('link',{name:/整理开发文档/})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'展开项目 Claude-named-folder'}))
  expect(screen.getByRole('link',{name:/整理开发文档/}).getAttribute('aria-current')).toBe('true')
  fireEvent.click(screen.getByRole('button',{name:'按时间'}))
  expect(screen.getByRole('link',{name:/整理开发文档/}).getAttribute('href')).toContain('session_id=ses_A')
  expect(screen.getByRole('link',{name:/整理开发文档/}).textContent).toContain('Claude-named-folder')
  fireEvent.click(screen.getByRole('button',{name:'按 Agent'}))
  fireEvent.change(screen.getByRole('textbox',{name:'搜索项目或路径'}),{target:{value:'/work/Claude'}})
  expect(screen.queryByText('Operations')).toBeNull()
  expect(screen.getByRole('link',{name:/Claude-named-folder/}).getAttribute('aria-current')).toBe('true')
  expect(screen.queryByRole('combobox',{name:'项目来源'})).toBeNull()
  fireEvent.change(screen.getByRole('textbox',{name:'搜索项目或路径'}),{target:{value:'not-a-project'}})
  expect(screen.getByText('没有匹配的项目或 Agent')).toBeTruthy()
})

it('opens one recent session, keeps it selected on new arrivals, and isolates a project switch', async () => {
  vi.useFakeTimers()
  const projects = [project('prj_A', 'First'), project('prj_B', 'Second')]
  let sessions = [session('ses_A','prj_A',1), session('ses_B','prj_A',2), session('ses_D','prj_B',0), session('ses_empty','prj_A',3)]
  const messages: Record<string, RawEvent[]> = {
    ses_A: [event(3,'user.prompt','OLD SESSION A','ses_A')],
    ses_B: [event(4,'user.prompt','SELECTED SESSION B','ses_B')],
    ses_C: [event(5,'user.prompt','NEW SESSION C','ses_C')],
    ses_D: [event(6,'user.prompt','OTHER PROJECT D','ses_D','prj_B')],
  }
  fake = installFakeCore({}, request => {
    if (request.path === '/_local/agents') return {body:{agents:[{id:'claude',name:'Claude',cli:true,desktop:false,detected:true,capture_supported:false}]}}
    if (request.path === '/v1/projects') { const rows=projects.slice(Number(request.query.get('offset') ?? 0));return { body:{projects:rows,count:rows.length} } }
    if (request.path === '/v1/sessions') { const rows=sessions.slice(Number(request.query.get('offset') ?? 0));return { body:{sessions:rows,count:rows.length} } }
    if (request.path === '/v1/tasks') return { body:{tasks:[],count:0} }
    if (request.path === '/v1/host-hooks/deliveries') return { body:{deliveries:[],count:0,has_more:false} }
    if (request.path === '/v1/events') {
      if(request.query.get('event_type') === 'project.created') return {body:{events:[],count:0}}
      const rows=(messages[request.query.get('session_id') ?? ''] ?? []).filter(row => row.seq > Number(request.query.get('after_seq') ?? 0))
      return {body:{events:rows,count:rows.length}}
    }
    return undefined
  })
  const { container, rerender } = render(<SessionProvider><ConversationView projectId={null} taskId={null} sessionId={null} /></SessionProvider>)
  await flush()
  expect(container.querySelector('.chat-messages')?.textContent).toContain('SELECTED SESSION B')
  expect(container.querySelector('.chat-messages')?.textContent).not.toContain('OLD SESSION A')
  fireEvent.click(screen.getByRole('button',{name:'按时间'}));await flush()
  expect(container.querySelector('.chat-messages')?.textContent).toContain('SELECTED SESSION B')
  expect(screen.getByRole('link',{name:/OTHER PROJECT D/})).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'按 Agent'}));await flush()
  sessions = [...sessions, session('ses_C','prj_A',4)]
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(container.querySelector('.chat-messages')?.textContent).toContain('SELECTED SESSION B')
  expect(screen.getByRole('link', { name: /NEW SESSION C/ })).toBeTruthy()
  rerender(<SessionProvider><ConversationView projectId="prj_B" taskId={null} sessionId={null} /></SessionProvider>)
  await flush()
  expect(container.querySelector('.chat-messages')?.textContent).toContain('OTHER PROJECT D')
  expect(container.querySelector('.chat-messages')?.textContent).not.toContain('SELECTED SESSION B')
  expect(fake.callsTo('GET','/_local/agents')).toHaveLength(1)
  expect(fake.callsTo('GET','/v1/events').every(request => request.query.has('session_id') || request.query.get('event_type') === 'project.created')).toBe(true)
})

it('does not fetch an unscoped message feed when no session is selected', async () => {
  vi.useFakeTimers()
  fake = installFakeCore({}, () => ({body:{events:[],count:0}}))
  const endpoints=makeEndpoints(new CoreClient({getToken:()=> 'tok_read'}))
  const {result,rerender}=renderHook(({enabled})=>useConversationEvents(endpoints,{session_id:'ses_A'},enabled),{initialProps:{enabled:false}})
  await flush();expect(fake.requests).toHaveLength(0);expect(result.current.loading).toBe(false)
  rerender({enabled:true});await flush();expect(fake.requests.length).toBe(1)
  rerender({enabled:false});await act(async()=>{await vi.advanceTimersByTimeAsync(6000)})
  expect(fake.requests.length).toBe(1);expect(result.current.items).toEqual([])
})

it('retries a just-started session until its first prompt arrives, masks the title and caches it', async () => {
  vi.useFakeTimers()
  let arrived = false
  const row = event(1, 'user.prompt', '连接配置 api_key=sk-abcdefghijklmnopqrstuvwxyz1234', 'ses_A')
  fake = installFakeCore({}, () => ({ body: { events: arrived ? [row] : [], count: arrived ? 1 : 0 } }))
  const endpoints=makeEndpoints(new CoreClient({getToken:()=> 'tok_read'}))
  const {result}=renderHook(()=>useSessionPreviews(endpoints,['ses_A']))
  await flush();expect(result.current.ses_A?.state).toBe('empty')
  arrived = true
  await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
  expect(result.current.ses_A?.state).toBe('ready')
  expect(result.current.ses_A?.title).not.toContain('sk-abcdefghijklmnopqrstuvwxyz1234')
  expect(result.current.ses_A?.agent).toBe('Codex')
  const count=fake.requests.length
  await act(async()=>{await vi.advanceTimersByTimeAsync(6000)})
  expect(fake.requests).toHaveLength(count)
})
