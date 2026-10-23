import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach,expect,it,vi } from 'vitest'
import { SessionProvider } from '../src/app/session'
import { RulesView } from '../src/views/RulesView'
import { RuleEditor } from '../src/components/RuleEditor'
import { installFakeCore,coreError,type FakeCore } from './helpers/fakeCore'
import type { Rule,Project } from '../src/api/types'
let fake:FakeCore|null=null
afterEach(()=>{fake?.restore();fake=null;window.location.hash=''})
const P='prj_01M4B6VGC29ESRDRQR62DX8AK8',T='tsk_01M4B6VGC29ESRDRQR62DX8AK8',E='evt_01M4B63ER9AR3M3V55FHRWZ66X',R='rul_01M4B6VGC29ESRDRQR62DX8AK8'
const project={project_id:P,name:'文档项目',description:'',status:'ACTIVE',revision:1,source_event_id:E,created_at:'',updated_at:''} satisfies Project
const rule={rule_id:R,rule_key:'machine.key.hidden',version:2,revision:5,status:'ACTIVE',scope:{kind:'project',project_id:P,task_id:null},kind:'RULE',severity:'NORMAL',enforcement:'CONTEXT',content:'项目文档统一使用中文。',matcher:{},verification_requirements:null,origin_event_id:E,origin_actor_kind:'human',origin_event_type:'user.prompt',change_event_id:E,status_event_id:E,status_actor_kind:'human'} satisfies Rule
const event={event_id:E,seq:1,event_type:'user.prompt',project_id:P,task_id:null,host_id:'hst_local',actor_kind:'human',payload:{text:'项目文档统一使用中文。'},recorded_at:'2026-10-23T08:00:00Z'}
const routes={'GET /v1/projects':{body:{projects:[project],count:1}},'GET /v1/tasks':{body:{tasks:[],count:0}},'GET /v1/hosts':{body:{hosts:[{host_id:'hst_local',display_name:'这台 Mac'}]}},'GET /v1/events':{body:{events:[event,{...event,event_id:'evt_other',task_id:T}],count:2}},['GET /v1/events/'+E]:{body:{event}},['GET /v1/projects/'+P]:{body:{project}},['GET /v1/rules/'+R+'/history']:{body:{history:[]}}}
function view(id:string|null=null){return render(<SessionProvider><RulesView projectId={null} taskId={null} ruleId={id}/></SessionProvider>)}
it('browses all-project approved rules without IDs, hides advanced forms and masks credential-shaped text',async()=>{
 fake=installFakeCore({...routes,'GET /v1/rules':{body:{rules:[{...rule,content:rule.content+' sk-abcdefghijklmnop1234567890'}],count:1,has_more:false,next_offset:null}}});view()
 const table=await screen.findByRole('table',{name:'规则列表'});expect(table.textContent).toContain('文档项目');expect(table.textContent).toContain('上下文约定');expect(table.textContent).toContain('已批准');expect(table.textContent).not.toContain('sk-abcdefghijklmnop');expect(table.textContent).not.toContain(P);expect(table.textContent).not.toContain('machine.key')
 expect(screen.queryByRole('region',{name:'Propose(提出新的 Rule)'})).toBeNull();expect(fake.callsTo('GET','/v1/host-hooks/deliveries')).toHaveLength(0)
 expect(fake.callsTo('GET','/v1/rules')[0]!.query.has('project_id')).toBe(false)
 fireEvent.change(screen.getByLabelText('按项目查看规则'),{target:{value:P}});await waitFor(()=>expect(fake!.callsTo('GET','/v1/rules').at(-1)!.query.get('project_id')).toBe(P))
})
it('keeps catalog failure distinct from an empty list',async()=>{
 fake=installFakeCore({...routes,'GET /v1/rules':coreError(403,'forbidden_scope','authority:read required')});view();await screen.findByRole('alert');expect(screen.queryByText('当前筛选下没有规则')).toBeNull()
})
it('proposes through readable project/source fields with the frozen creation body and no implicit approval',async()=>{
 const user=userEvent.setup(),saved=vi.fn();fake=installFakeCore({...routes,'POST /v1/rules/proposals':{status:201,body:{rule:{...rule,status:'PROPOSED'}}}})
 render(<SessionProvider><RuleEditor open onOpenChange={()=>{}} rule={null} projectId={P} taskId={null} projects={[project]} tasks={[]} onSaved={saved}/></SessionProvider>)
 await screen.findByRole('option',{name:/用户：项目文档/});expect(screen.queryByRole('option',{name:/evt_other/})).toBeNull()
 await user.selectOptions(screen.getByLabelText('依据哪条对话或记录'),E);await user.type(screen.getByLabelText('规则内容'),'文档统一使用中文。');await user.click(screen.getByRole('button',{name:'提交，等待批准'}));await waitFor(()=>expect(saved).toHaveBeenCalledWith(R))
 const body=fake.callsTo('POST','/v1/rules/proposals')[0]!.body!;expect(body).toMatchObject({origin_event_id:E,host_id:'hst_local',enforcement:'CONTEXT',scope:{kind:'project',project_id:P,task_id:null}});expect(Object.keys(body).sort()).toEqual(['rule_key','scope','origin_event_id','host_id','kind','severity','enforcement','content','matcher','event_id'].sort());expect(fake.requests.filter(r=>r.method==='POST')).toHaveLength(1)
})
it('explicit approval uses the read revision and never enables Guard',async()=>{
 let current:Rule={...rule,status:'PROPOSED',revision:1};fake=installFakeCore(routes,req=>{
  if(req.path==='/v1/rules')return {body:{rules:[current],count:1,has_more:false,next_offset:null}}
  if(req.path==='/v1/rules/'+R&&req.method==='GET')return {body:{rule:current}}
  if(req.path==='/v1/rules/'+R+'/approve'){current={...current,status:'ACTIVE',revision:2};return {body:{rule:current}}}
 });view(R)
 const button=await screen.findByRole('button',{name:'批准这条规则'});await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));fireEvent.click(button)
 await screen.findByRole('button',{name:'修改规则'});const posts=fake.requests.filter(r=>r.method==='POST');expect(posts).toHaveLength(1);expect(Object.keys(posts[0]!.body!).sort()).toEqual(['event_id','host_id','expected_revision'].sort());expect(posts[0]!.body!.expected_revision).toBe(1)
})
it('editing preserves verification requirements, matcher, scope and current revision',async()=>{
 const user=userEvent.setup(),saved=vi.fn(),existing:Rule={...rule,kind:'ACCEPTANCE',enforcement:'VERIFY',matcher:{tool_category:'shell'},verification_requirements:{requirements:[{key:'build',kind:'BUILD',required_result:'PASS'}]}}
 fake=installFakeCore({...routes,['POST /v1/rules/'+R+'/supersede']:{body:{rule:{...existing,revision:6}}}})
 render(<SessionProvider><RuleEditor open onOpenChange={()=>{}} rule={existing} projectId={P} taskId={null} projects={[project]} tasks={[]} onSaved={saved}/></SessionProvider>)
 await screen.findByRole('option',{name:/用户：项目文档/});await user.selectOptions(screen.getByLabelText('依据哪条对话或记录'),E);await user.click(screen.getByRole('button',{name:'保存新版本'}));await waitFor(()=>expect(saved).toHaveBeenCalledWith(R))
 expect(fake.callsTo('POST','/v1/rules/'+R+'/supersede')[0]!.body).toMatchObject({scope:existing.scope,matcher:existing.matcher,verification_requirements:existing.verification_requirements,expected_revision:5})
})
