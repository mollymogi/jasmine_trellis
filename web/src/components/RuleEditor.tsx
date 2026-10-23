/** Guided Authority writes preserve source scope, revision and replay identity. */
import { useEffect,useRef,useState } from 'react'
import type { Project,RawEvent,Rule,Task } from '../api/types'
import { useSession } from '../app/session'
import { usePagedResource } from '../app/usePagedResource'
import { useResource } from '../app/useResource'
import { useWriteAction } from '../app/useWriteAction'
import { newEventId } from '../lib/ids'
import { formatTimestamp,maskCredentials } from '../lib/text'
import { RULE_KINDS,RULE_USES,TOOL_LABELS } from '../lib/rulePresentation'
import { Dialog,DialogContent,DialogDescription,DialogHeader,DialogTitle } from './ui/dialog'
import { Button } from './ui/button'
import { JsonBlock } from './JsonBlock'
import { ErrorPanel } from './States'
const newKey=()=>`manual.${newEventId().slice(4).toLowerCase()}`
export function RuleEditor({open,onOpenChange,rule,projectId,taskId,projects,onSaved}:{open:boolean;onOpenChange:(v:boolean)=>void;rule:Rule|null;projectId:string|null;taskId:string|null;projects:Project[];tasks:Task[];onSaved:(id:string)=>void}){
 const {endpoints}=useSession()
 const [project,setProject]=useState(rule?(rule.scope.project_id??'global'):(projectId??'')),[task,setTask]=useState(rule?.scope.task_id??taskId??''),[origin,setOrigin]=useState(''),[content,setContent]=useState(rule?.content??''),[kind,setKind]=useState(rule?.kind??'RULE'),[enforcement,setEnforcement]=useState(rule?.enforcement??'CONTEXT'),[hard,setHard]=useState(rule?.severity==='HARD')
 const [category,setCategory]=useState(String(rule?.matcher.tool_category??'')),[path,setPath]=useState(String(rule?.matcher.path_prefix??'')),[operation,setOperation]=useState(String(rule?.matcher.action??'')),[tool,setTool]=useState(String(rule?.matcher.tool??'')),[host,setHost]=useState(''),[allSources,setAllSources]=useState(false),[ruleKey,setRuleKey]=useState(newKey),[validation,setValidation]=useState('')
 const hosts=useResource(open?signal=>endpoints.listHosts(signal):null,[open])
 const tasks=usePagedResource<Task>(open&&project&&project!=='global'?async(cursor,signal)=>{const offset=Number(cursor??0),p=await endpoints.listTasks(project,{limit:100,offset},signal);return {items:p.tasks,count:p.count,complete:p.count<100,nextCursor:p.count<100?null:offset+100}}:null,[open,project])
 const events=usePagedResource<RawEvent>(open&&project?async(cursor,signal)=>{const p=await endpoints.listEvents({project_id:project==='global'?null:project,task_id:task||null,event_type:allSources?null:'user.prompt',after_seq:cursor===null?null:Number(cursor),limit:50},signal);return {items:p.events.filter(e=>e.project_id===(project==='global'?null:project)&&e.task_id===(task||null)),count:p.count,complete:p.count<50,nextCursor:p.count<50?null:p.events.at(-1)?.seq??null}}:null,[open,project,task,allSources])
 const action=useWriteAction((body,signal)=>rule?endpoints.supersedeRule(rule.rule_id,body,signal):endpoints.proposeRule(body,signal),{keyKind:'event_id',targetKey:rule?.rule_id??'new'})
 const handled=useRef<unknown>(null)
 useEffect(()=>{if(open&&!rule&&!content&&!origin&&action.state==='idle'){setProject(projectId??'');setTask(taskId??'')}},[open])
 useEffect(()=>{if(!action.result||handled.current===action.result)return;handled.current=action.result;const id=action.result.rule?.rule_id;if(typeof id==='string'){if(!rule){setContent('');setOrigin('');setKind('RULE');setEnforcement('CONTEXT');setHard(false);setCategory('');setPath('');setOperation('');setTool('');setRuleKey(newKey());action.resetDraft()}onSaved(id)}},[action.result])
 const hostId=host||hosts.data?.hosts[0]?.host_id||''
 const changeScope=(p:string,t:string)=>{setProject(p);setTask(t);setOrigin('');setValidation('')}
 const matcher=Object.fromEntries([['tool_category',category],['path_prefix',path.trim()],['action',operation.trim()],['tool',tool.trim()]].filter(([,v])=>!!v))
 async function submit(){
  if(!project||!origin||!events.items.some(e=>e.event_id===origin)||!content.trim()||!hostId){setValidation('请选择适用范围和来源，并填写规则内容。');return}
  if(content.length>4096){setValidation('规则内容最多 4096 个字符。');return}
  if(path&&(!path.startsWith('/')||path.includes('//')||path.includes('\\')||path.includes('\0')||path.split('/').some(p=>p==='.'||p==='..')||(path!=='/'&&path.endsWith('/')))){setValidation('匹配路径需要规范的绝对路径，不包含 .. 或末尾斜杠。');return}
  if(['DENY','CONFIRM'].includes(enforcement)&&!Object.keys(matcher).length){setValidation('禁止执行和执行前确认需要至少一个明确的工具、操作或路径条件。');return}
  if(rule?.verification_requirements&&kind!=='ACCEPTANCE'&&enforcement!=='VERIFY'){setValidation('此规则有专用验收条件，请保留验收类型或在高级管理中调整。');return}
  setValidation('')
  const body={scope:rule?.scope??{kind:project==='global'?'global':task?'task':'project',project_id:project==='global'?null:project,task_id:task||null},origin_event_id:origin,host_id:hostId,kind:['DENY','CONFIRM'].includes(enforcement)?'RULE':kind,severity:hard?'HARD':'NORMAL',enforcement,content,matcher,...(rule?.verification_requirements?{verification_requirements:rule.verification_requirements}:{})}
  await action.submit(rule?{...body,expected_revision:rule.revision}:{...body,rule_key:ruleKey})
 }
 function sourceLabel(e:RawEvent){const text=typeof e.payload.text==='string'?e.payload.text:typeof e.payload.title==='string'?e.payload.title:'来源记录';return `${formatTimestamp(e.recorded_at)} · ${e.event_type==='user.prompt'?(e.actor_kind==='human'?'用户':'Agent'):'记录'}：${maskCredentials(text).text.slice(0,90)}`}
 return <Dialog open={open} onOpenChange={next=>{if(!action.busy)onOpenChange(next)}}><DialogContent className="rules-dialog rule-editor"><DialogHeader><DialogTitle>{rule?'修改规则':'提出一条规则'}</DialogTitle><DialogDescription>{rule?'保存会追加新版本并替换当前版本，原有历史保留。':'选择一条已有对话作为依据，写成清晰的约定。提交后仍需单独批准。'}</DialogDescription></DialogHeader>
 <fieldset disabled={action.busy} className="rule-editor-fields"><div className="rules-field-grid"><label>适用项目<select aria-label="适用项目" value={project} disabled={!!rule} onChange={e=>changeScope(e.target.value,'')}><option value="">请选择项目</option><option value="global">所有项目（全局约定）</option>{project&&project!=='global'&&!projects.some(p=>p.project_id===project)?<option value={project}>原项目</option>:null}{projects.map(p=><option value={p.project_id} key={p.project_id}>{p.name}</option>)}</select></label>{project&&project!=='global'?<label>适用任务<select aria-label="适用任务" value={task} disabled={!!rule} onChange={e=>changeScope(project,e.target.value)}><option value="">整个项目</option>{task&&!tasks.items.some(t=>t.task_id===task)?<option value={task}>原任务</option>:null}{tasks.items.map(t=><option value={t.task_id} key={t.task_id}>{t.title}</option>)}</select></label>:null}</div>
 {tasks.hasMore?<Button size="sm" variant="ghost" onClick={tasks.loadMore}>载入更多任务</Button>:null}
 <label>依据哪条对话或记录<select aria-label="依据哪条对话或记录" value={origin} onChange={e=>setOrigin(e.target.value)}><option value="">请选择来源</option>{events.items.map(e=><option value={e.event_id} key={e.event_id}>{sourceLabel(e)}</option>)}</select></label>
 {project&&!events.loading&&!events.error&&!events.items.length?<p className="muted">当前已载入的来源中没有适合此范围的对话。可以载入更多，或包含其他来源记录。</p>:null}
 <div className="processing-actions">{events.hasMore?<Button variant="ghost" size="sm" disabled={events.loading} onClick={events.loadMore}>载入更多来源</Button>:null}<Button variant="ghost" size="sm" onClick={()=>{setAllSources(v=>!v);setOrigin('')}}>{allSources?'仅显示用户消息':'包含其他来源记录'}</Button></div>
 {hosts.error?<ErrorPanel error={hosts.error} what="设备信息" onRetry={hosts.reload}/>:null}{tasks.error?<ErrorPanel error={tasks.error} what="任务信息" onRetry={tasks.reload}/>:null}
 {events.error?<ErrorPanel error={events.error} what="来源记录" onRetry={events.reload}/>:null}
 <label>规则内容<textarea aria-label="规则内容" rows={4} maxLength={4096} placeholder="例如：项目文档默认使用中文。" value={content} onChange={e=>setContent(e.target.value)}/></label>
 <div className="rules-field-grid"><label>用途<select aria-label="用途" value={enforcement} onChange={e=>{setEnforcement(e.target.value);if(['DENY','CONFIRM'].includes(e.target.value))setKind('RULE')}}>{Object.entries(RULE_USES).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label><label>内容类型<select aria-label="内容类型" value={kind} disabled={['DENY','CONFIRM'].includes(enforcement)} onChange={e=>setKind(e.target.value)}>{Object.entries(RULE_KINDS).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label></div>
 {['DENY','CONFIRM'].includes(enforcement)?<div className="rules-note">这类规则需要明确的匹配条件，并依赖 Agent 的执行前检查。保存或批准不会自动开启 Guard。</div>:null}
 <details open={['DENY','CONFIRM'].includes(enforcement)}><summary>工具与路径条件</summary><div className="rules-field-grid"><label>适用工具类别<select aria-label="适用工具类别" value={category} onChange={e=>setCategory(e.target.value)}><option value="">不限类别</option>{Object.entries(TOOL_LABELS).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label><label>匹配路径<input value={path} onChange={e=>setPath(e.target.value)} placeholder="绝对路径，可选" maxLength={512}/></label><label>操作名称<input value={operation} onChange={e=>setOperation(e.target.value)} placeholder="如 edit、rm，可选" maxLength={512}/></label><label>原生工具名称<input value={tool} onChange={e=>setTool(e.target.value)} placeholder="精确工具名，可选" maxLength={512}/></label></div><p className="muted">填写的条件须同时满足。路径包括其子路径；只填路径时，不区分读取、写入等操作。</p></details>
 <details><summary>更多选项</summary><label className="rule-checkbox"><input type="checkbox" checked={hard} onChange={e=>setHard(e.target.checked)}/>标记为强约束</label><label>记录操作的设备<select aria-label="记录操作的设备" value={hostId} onChange={e=>setHost(e.target.value)}>{hosts.data?.hosts.map(h=><option value={h.host_id} key={h.host_id}>{h.display_name||'本机设备'}</option>)}</select></label>{rule?.verification_requirements?<p>原有的专用验收条件将保留；可在高级管理中调整。</p>:null}</details>
 </fieldset>
 {validation?<p role="alert">{validation}</p>:null}{action.state==='conflict'?<p role="alert">规则或提交依据已变化，草稿已保留。请重新读取当前规则后核对，不会自动覆盖。</p>:action.state==='refused'?<p role="alert">提交未获准。请核对凭证权限、来源范围及当前规则状态。</p>:action.state==='unknown'?<p role="alert">提交结果尚未确认。请先读取规则列表核对，重试会沿用本次提交标识。</p>:null}
 {action.error?<details><summary>查看失败详情</summary><JsonBlock value={{code:action.error.code,request_id:action.requestId,details:action.error.details}} label="提交结果"/></details>:null}
 <div className="interpreter-footer"><Button variant="ghost" disabled={action.busy} onClick={()=>onOpenChange(false)}>取消</Button><Button disabled={action.busy||!project||!origin||!content.trim()||!hostId} onClick={()=>void submit()}>{action.busy?'正在提交…':rule?'保存新版本':'提交，等待批准'}</Button></div>
 </DialogContent></Dialog>
}
