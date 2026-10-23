import { fireEvent,render,screen,waitFor,within } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import { ConversationProcessing } from '../src/components/ConversationProcessing'
import { InterpretationsView } from '../src/views/InterpretationsView'
import { StatusBadge } from '../src/components/StatusBadge'
import { processingPreview,processingState,type ProcessingItem } from '../src/lib/processingPresentation'
const endpoints=vi.hoisted(()=>({listInterpretations:vi.fn().mockResolvedValue({items:[],next_after_id:null})}))
vi.mock('../src/app/session',()=>({useSession:()=>({token:'operator',endpoints})}))
afterEach(()=>{vi.unstubAllGlobals();vi.clearAllMocks()})
const row:ProcessingItem={event_id:'evt_machine_source',status:'APPLIED',source_text:'项目文档使用中文',source_actor_kind:'human',host_id:'hst_machine',project_id:'prj_machine',project_name:'我的项目',session_id:'ses_machine',event_type:'user.prompt',source_system:'grok-native-conversation-v1',interpretation_id:'int_machine',resolution_id:'res_machine',review_id:null,queued_at:'2026-10-23T04:35:00Z',error_code:null,effect:'MEMORY_ELIGIBLE',extractor_model:'gpt-6.1-sol',candidates:[],memory_items:[{memory_id:'mem_machine',memory_type:'STABLE_PROJECT_FACT',engine_state:'RETAINED',status:'ACTIVE'}],memory_jobs:[{job_id:'mjb_machine',status:'DONE',last_error_code:null}]}
function serve(){vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({items:[row],count:1,counts:{},worker_configured:true,started_at:'2026-10-23T00:00:00Z',last_tick_at:new Date().toISOString()})}))}
it('shows only human list columns and uses the recorded Agent, not the extraction model',async()=>{
 serve();render(<ConversationProcessing/>);const table=await screen.findByRole('table',{name:'对话提炼记录'});expect(within(table).getAllByRole('columnheader').map(n=>n.textContent)).toEqual(['序号','时间','Agent','来源','内容摘要','状态','操作']);expect(within(table).getByText('Grok')).toBeTruthy();expect(within(table).getByText('我的项目')).toBeTruthy();expect(within(table).getByText('已入库')).toBeTruthy();expect(table.querySelector('time')?.getAttribute('datetime')).toBe(row.queued_at);expect(table.textContent).not.toMatch(/evt_machine|int_machine|gpt-6\.1|APPLIED|RETAINED|DONE/)
})
it('keeps source IDs and model metadata behind an explicitly closed technical disclosure',async()=>{
 serve();render(<ConversationProcessing/>);fireEvent.click(await screen.findByRole('button',{name:'查看详情'}));const dialog=await screen.findByRole('dialog');expect(within(dialog).getByText('对话原文')).toBeTruthy();const technical=within(dialog).getByText('技术详情').closest('details')!;expect(technical.open).toBe(false);expect(within(dialog).getByRole('link',{name:'查看记忆'}).getAttribute('href')).toContain('mem_machine');fireEvent.click(within(dialog).getByText('技术详情'));await waitFor(()=>expect(technical.open).toBe(true));expect(within(technical).getByText('evt_machine_source')).toBeTruthy();expect(within(technical).getByRole('link',{name:'写入任务：已完成'}).getAttribute('href')).toContain('mjb_machine')
})
it('translates a known Core status while preserving its exact automation token',()=>{
 const {container}=render(<StatusBadge kind="interpretation" status="EXTRACTED"/>);expect(screen.getByText('已提取')).toBeTruthy();expect(container.querySelector('[data-status=EXTRACTED]')).toBeTruthy();expect(screen.queryByText('EXTRACTED')).toBeNull()
})
it('does not infer success from an unknown status',()=>{
 expect(processingState({...row,status:'NEW_STATE',memory_items:[],memory_jobs:[]}).label).toBe('状态待确认')
})
it('removes the browser wrapper only from a preview and preserves the source',()=>{
 const source='<in-app-browser-context source="ambient-ui-state">technical context</in-app-browser-context>\n## My request:\n你好';const item={...row,source_text:source};expect(processingPreview(item)).toBe('你好');expect(item.source_text).toBe(source)
})

it('loads low-level interpretation records only after the human explicitly opens technical records',async()=>{
 serve();render(<InterpretationsView projectId={null} taskId={null} eventId={null} status={null}/>);await screen.findByRole('table',{name:'对话提炼记录'});expect(endpoints.listInterpretations).not.toHaveBeenCalled();expect(screen.queryByText('evt_machine_source')).toBeNull();fireEvent.click(screen.getByText('技术记录'));await waitFor(()=>expect(endpoints.listInterpretations).toHaveBeenCalledOnce())
})
