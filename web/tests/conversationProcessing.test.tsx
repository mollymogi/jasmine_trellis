import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import { ConversationProcessing } from '../src/components/ConversationProcessing'
const api=vi.hoisted(()=>({getReview:vi.fn(),approveReview:vi.fn()}))
vi.mock('../src/app/session',()=>({useSession:()=>({token:'operator',endpoints:api})}))
afterEach(()=>{vi.unstubAllGlobals();vi.clearAllMocks()})
const candidate={kind:'RULE',content:'项目文档使用中文',scope:{kind:'PROJECT',project_id:'prj_test',task_id:null,path:null,tool:null},certainty:'EXPLICIT',impact:'LOW',confidence:.95,rationale:'明确约定',source_span:{start:0,end:4,quote:'原话依据'}}
const row={event_id:'evt_source',status:'PENDING_REVIEW',source_text:'原始对话保留',source_actor_kind:'human',event_type:'user.prompt',host_id:'hst_local',project_id:'prj_test',session_id:'ses_test',source_system:'codex-native-conversation-v1',interpretation_id:'int_test',resolution_id:'res_test',review_id:'rvw_test',error_code:null,effect:'NONE',candidates:[candidate],memory_items:[],memory_jobs:[]}
const page=(items:unknown[])=>({items,count:items.length,counts:{},worker_configured:true,last_tick_at:new Date().toISOString(),started_at:'2026-10-23T00:00:00Z'})
function serve(items:unknown[]){const f=vi.fn().mockImplementation(()=>Promise.resolve({ok:true,json:async()=>page(items)}));vi.stubGlobal('fetch',f);return f}
it('keeps a candidate pending until the server reports actual retained memory',async()=>{
 serve([row]);render(<ConversationProcessing/>);await screen.findByText('待审核');expect(screen.queryByText('已入库')).toBeNull();expect(screen.getByRole('button',{name:'审核项目约定'})).toBeTruthy()
 fireEvent.click(screen.getByText('来源与处理细节'));expect(screen.getByRole('link',{name:'查看原始 Event'}).getAttribute('href')).toContain('evt_source')
})
it('queues only the explicitly selected session and reuses stored messages',async()=>{
 const f=serve([]);render(<ConversationProcessing projectId="prj_test" sessionId="ses_test"/>);await screen.findByText('尚无分析记录。新消息会自动进入；可在 Conversation 选中会话，分析已收录的历史消息。')
 fireEvent.click(screen.getByRole('button',{name:'分析本次会话'}));await waitFor(()=>expect(f.mock.calls.some(call=>call[1]?.method==='POST')).toBe(true))
 const post=f.mock.calls.find(call=>call[1]?.method==='POST');expect(post?.[0]).toBe('/v1/conversation-processing/enqueue');expect(JSON.parse(post?.[1].body)).toEqual({session_id:'ses_test'})
})
it('retries a failed event without sending its text or selecting a provider',async()=>{
 const f=serve([{...row,status:'FAILED',review_id:null,error_code:'provider_timeout'}]);render(<ConversationProcessing/>);await screen.findByText('分析失败');fireEvent.click(screen.getByRole('button',{name:'重新分析'}))
 await waitFor(()=>expect(f.mock.calls.some(call=>call[0]==='/v1/conversation-processing/evt_source/retry'&&call[1]?.body==='{}')).toBe(true))
})
it('requires a fresh preview and explicit confirmation before approving project truth',async()=>{
 serve([row]);api.getReview.mockResolvedValue({review:{review_id:'rvw_test',revision:3,status:'PENDING',preview:{expected_revisions:[{object_id:'prj_test',object_type:'project',revision:2}],expected_context_digest:'a'.repeat(64),plan:{items:[{candidate_index:0,candidate}]}}}});api.approveReview.mockResolvedValue({})
 render(<ConversationProcessing/>);await screen.findByText('待审核');fireEvent.click(screen.getByRole('button',{name:'审核项目约定'}));const button=await screen.findByRole('button',{name:'批准此项目约定'});await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));expect(api.approveReview).not.toHaveBeenCalled();fireEvent.click(button)
 await waitFor(()=>expect(api.approveReview).toHaveBeenCalledOnce());const body=api.approveReview.mock.calls[0]![1];expect(body.host_id).toBe('hst_local');expect(body.expected_revision).toBe(3);expect(body.expected_context_digest).toBe('a'.repeat(64));expect(body.actions[0]).toEqual({candidate_index:0,action:'CREATE_RULE',payload:{kind:'RULE',severity:'NORMAL',enforcement:'CONTEXT',content:'项目文档使用中文',matcher:{}}})
})
it('does not offer shortcut approval for generated claims or high-impact candidates',async()=>{
 serve([{...row,source_actor_kind:'system',event_type:'assistant.message',source_system:'grok-native-conversation-v1'},{...row,event_id:'evt_high',candidates:[{...candidate,impact:'HIGH'}]}]);render(<ConversationProcessing/>);await screen.findAllByText('待审核');expect(screen.queryByRole('button',{name:'审核项目约定'})).toBeNull()
})
it('distinguishes no structure, task-only application, pending retain, failure and retained memory',async()=>{
 serve([{...row,event_id:'evt_no',status:'NO_ACTION',candidates:[],review_id:null},{...row,event_id:'evt_task',status:'APPLIED',effect:'TASK_ONLY',review_id:null},{...row,event_id:'evt_pending',status:'APPLIED',effect:'MEMORY_ELIGIBLE',review_id:null},{...row,event_id:'evt_failed',status:'APPLIED',memory_jobs:[{job_id:'mjb_failed',status:'FAILED',last_error_code:'engine_error'}]},{...row,event_id:'evt_done',status:'APPLIED',memory_items:[{memory_id:'mem_done',memory_type:'STABLE_PROJECT_FACT',engine_state:'RETAINED',status:'ACTIVE'}]}]);render(<ConversationProcessing/>);for(const label of ['无需记忆','已处理','等待入库','写入失败','已入库'])await screen.findByText(label)
})

it('reports a stale worker separately from configured automatic processing',async()=>{
 const f=serve([]);f.mockImplementation(()=>Promise.resolve({ok:true,json:async()=>({...page([]),last_tick_at:'2000-01-01T00:00:00Z'})}));render(<ConversationProcessing/>);await screen.findByText('自动分析暂未响应，已收录的消息和处理记录仍保留。')
})
