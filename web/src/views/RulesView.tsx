/** Human catalog; effective-rule lookup and immutable Authority writes keep their contracts. */
import { useEffect,useState } from 'react'
import { ListChecks,Plus,RefreshCw,Search,ShieldCheck } from 'lucide-react'
import type { Project,Rule,Task } from '../api/types'
import { hrefFor,sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { usePagedResource } from '../app/usePagedResource'
import { useWriteAction } from '../app/useWriteAction'
import { Button } from '../components/ui/button'
import { Badge } from '../components/ui/badge'
import { Dialog,DialogContent,DialogDescription,DialogHeader,DialogTitle } from '../components/ui/dialog'
import { RawText } from '../components/RawText'
import { JsonBlock } from '../components/JsonBlock'
import { ErrorPanel } from '../components/States'
import { RuleGuide } from '../components/RuleGuide'
import { RuleEditor } from '../components/RuleEditor'
import { RuleWrites } from './RuleWrites'
import { HookDecisions } from '../components/HookDecisions'
import { formatTimestamp,maskCredentials } from '../lib/text'
import { RULE_STATES,RULE_USES,RULE_KINDS,rulePreview,ruleTarget } from '../lib/rulePresentation'

export function RulesView({projectId,taskId,ruleId}:{projectId:string|null;taskId:string|null;ruleId:string|null}){
 const {endpoints}=useSession()
 const [scope,setScope]=useState(projectId??'all'),[task,setTask]=useState(taskId??''),[status,setStatus]=useState('ACTIVE'),[search,setSearch]=useState(''),[create,setCreate]=useState(false),[editing,setEditing]=useState<Rule|null>(null),[advanced,setAdvanced]=useState(false),[decisions,setDecisions]=useState(false),[detailBusy,setDetailBusy]=useState(false)
 useEffect(()=>{setScope(projectId??'all');setTask(taskId??'')},[projectId,taskId])
 const project=scope==='all'||scope==='global'?null:scope
 const projects=usePagedResource<Project>(async(cursor,signal)=>{const offset=Number(cursor??0),p=await endpoints.listProjects({limit:100,offset},signal);return {items:p.projects,count:p.count,complete:p.count<100,nextCursor:p.count<100?null:offset+100}},[])
 const tasks=usePagedResource<Task>(async(cursor,signal)=>{const offset=Number(cursor??0),p=await endpoints.listTasks(project,{limit:100,offset},signal);return {items:p.tasks,count:p.count,complete:p.count<100,nextCursor:p.count<100?null:offset+100}},[project])
 const rules=usePagedResource<Rule>(async(cursor,signal)=>{const p=await endpoints.listRules({project_id:project,task_id:task||null,scope_kind:scope==='global'?'global':null,status:status||null,limit:50,offset:Number(cursor??0)},signal);return {items:p.rules,count:p.count,complete:!p.has_more,nextCursor:p.next_offset}},[project,task,status,scope==='global'])
 const [lookup,setLookup]=useState('')
 const detail=useResource(ruleId?(signal)=>endpoints.getRule(ruleId,signal):null,[ruleId])
 const reload=()=>{rules.reload();detail.reload()}
 const show=(id:string|null)=>{globalThis.location.hash=hrefFor({name:'rules',projectId:project,taskId:task||null,ruleId:id})}
 const visible=rules.items.filter(r=>rulePreview(r).toLocaleLowerCase().includes(search.toLocaleLowerCase()))
 const scopeLabel=(r:Rule)=>r.scope.kind==='global'?'所有项目':projects.items.find(p=>p.project_id===r.scope.project_id)?.name??'项目规则'
 const taskLabel=(r:Rule)=>r.scope.kind==='task'?(tasks.items.find(t=>t.task_id===r.scope.task_id)?.title??'指定任务'):r.scope.kind==='global'?'全局约定':'整个项目'
 return <section className="rules-page" aria-label="规则管理">
  <div className="processing-heading"><div><h2><ListChecks size={18}/>规则<span className="processing-heading-count">已载入 {rules.items.length} 条</span></h2><p>查看当前约定，决定哪些内容生效、修改或停用。</p></div><div className="processing-actions"><RuleGuide/><Button size="sm" variant="outline" onClick={reload}><RefreshCw size={14}/>刷新</Button><Button size="sm" onClick={()=>setCreate(true)}><Plus size={14}/>新增规则</Button></div></div>
  <div className="rules-note"><ShieldCheck size={16}/><span>已批准 ≠ 已开启拦截。上下文约定需注入 Agent；禁止执行和执行前确认还需接入 Guard。<a href="#/reviews/pending"> 查看待审核建议</a></span></div>
  <div className="rules-toolbar"><label className="rules-search"><Search size={15}/><span className="sr-only">搜索已载入规则</span><input placeholder="搜索规则内容" value={search} onChange={e=>setSearch(e.target.value)}/></label><label><span className="sr-only">按项目查看规则</span><select aria-label="按项目查看规则" value={scope} onChange={e=>{setScope(e.target.value);setTask('')}}><option value="all">全部项目</option><option value="global">仅全局约定</option>{project&&!projects.items.some(p=>p.project_id===project)?<option value={project}>当前项目</option>:null}{projects.items.map(p=><option key={p.project_id} value={p.project_id}>{p.name}</option>)}</select></label><label><span className="sr-only">规则状态</span><select aria-label="规则状态" value={status} onChange={e=>setStatus(e.target.value)}><option value="">全部状态</option><option value="ACTIVE">已批准</option><option value="PROPOSED">待批准</option><option value="RETIRED">已停用</option></select></label>{project?<label><span className="sr-only">按任务查看规则</span><select aria-label="按任务查看规则" value={task} onChange={e=>setTask(e.target.value)}><option value="">项目下全部任务</option>{task&&!tasks.items.some(t=>t.task_id===task)?<option value={task}>当前任务</option>:null}{tasks.items.map(t=><option value={t.task_id} key={t.task_id}>{t.title}</option>)}</select></label>:null}</div>
  {(projects.error||tasks.error)?<p className="muted">部分项目或任务名称未能加载，规则内容仍按当前查询显示。<Button variant="ghost" size="sm" onClick={()=>{projects.reload();tasks.reload()}}>重试名称读取</Button></p>:null}
  {(projects.hasMore||tasks.hasMore)?<div className="processing-actions">{projects.hasMore?<Button size="sm" variant="ghost" onClick={projects.loadMore} disabled={projects.loading}>载入更多项目</Button>:null}{tasks.hasMore?<Button size="sm" variant="ghost" onClick={tasks.loadMore} disabled={tasks.loading}>载入更多任务名称</Button>:null}</div>:null}
  {rules.error?<ErrorPanel error={rules.error} what="规则列表" onRetry={rules.reload}/>:null}
  {rules.loading&&!rules.items.length?<p role="status" className="rules-empty">正在读取规则…</p>:!rules.error&&!visible.length?<div className="rules-empty"><ListChecks size={28}/><h3>{search?'已载入的规则中没有匹配内容':'当前筛选下没有规则'}</h3><p>{search?'换个关键词，或继续载入更多规则。':'可以切换项目或状态；对话提炼的建议需要先到“待审核”确认。'}</p><Button asChild variant="outline"><a href="#/reviews/pending">查看待审核建议</a></Button></div>:null}
  {visible.length?<div className="rules-table"><table aria-label="规则列表"><thead><tr><th>规则内容</th><th>适用范围</th><th>用途</th><th>状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{visible.map(r=><tr key={r.rule_id}><td data-label="规则内容"><button className="rule-content" onClick={()=>show(r.rule_id)}>{rulePreview(r)}</button>{r.severity==='HARD'?<span className="rule-hard">强约束</span>:null}</td><td data-label="适用范围"><span className="rule-project">{maskCredentials(scopeLabel(r)).text}</span><span className="rule-secondary">{maskCredentials(taskLabel(r)).text}</span></td><td data-label="用途">{RULE_USES[r.enforcement]??r.enforcement}</td><td data-label="状态"><Badge variant={r.status==='ACTIVE'?'success':r.status==='PROPOSED'?'warning':'outline'}>{RULE_STATES[r.status]??r.status}</Badge></td><td data-label="操作"><Button size="sm" variant="ghost" onClick={()=>show(r.rule_id)}>查看详情</Button></td></tr>)}</tbody></table></div>:null}
  {rules.hasMore?<Button variant="outline" onClick={rules.loadMore} disabled={rules.loading}>{rules.loading?'正在载入…':'载入更多规则'}</Button>:null}
  <div className="rules-secondary-actions"><Button variant="ghost" size="sm" onClick={()=>setDecisions(v=>!v)}>{decisions?'收起拦截记录':'拦截记录'}</Button><Button variant="ghost" size="sm" onClick={()=>setAdvanced(v=>!v)}>{advanced?'收起高级管理':'高级管理'}</Button></div>
  {decisions?<HookDecisions/>:null}
  {advanced?<details open className="rules-advanced"><summary>技术管理与兼容操作</summary><p className="muted">用于精确读取规则及高级匹配、验收条件。常规操作可直接使用列表和详情。</p><div className="filters"><label>规则编号<input value={lookup} onChange={e=>setLookup(e.target.value)} placeholder="rul_…"/></label><Button variant="outline" onClick={()=>{if(lookup.trim())show(lookup.trim())}}>按编号读取</Button></div><RuleWrites rule={detail.data?.rule??null} projectId={project} taskId={task||null} onReadback={reload}/></details>:null}
  <Dialog open={!!ruleId&&!advanced} onOpenChange={open=>{if(!open&&!detailBusy)show(null)}}><DialogContent className="rules-dialog rule-detail" showCloseButton={!detailBusy}><DialogHeader><DialogTitle>规则详情</DialogTitle><DialogDescription>查看这条约定的范围、用途和来源。</DialogDescription></DialogHeader>{detail.error?<ErrorPanel error={detail.error} what="规则详情" onRetry={detail.reload}/>:detail.data?<RuleDetail key={detail.data.rule.rule_id} rule={detail.data.rule} onRefresh={reload} onBusyChange={setDetailBusy} onEdit={()=>{setEditing(detail.data!.rule);show(null)}}/>:<p>正在读取规则…</p>}<Button size="sm" variant="ghost" disabled={detailBusy} onClick={()=>setAdvanced(true)}>高级管理</Button></DialogContent></Dialog>
  <RuleEditor open={create} onOpenChange={setCreate} rule={null} projectId={project} taskId={task||null} projects={projects.items} tasks={tasks.items} onSaved={id=>{setCreate(false);rules.reload();show(id)}}/>
  {editing?<RuleEditor key={editing.rule_id} open onOpenChange={open=>{if(!open)setEditing(null)}} rule={editing} projectId={project} taskId={task||null} projects={projects.items} tasks={tasks.items} onSaved={id=>{setEditing(null);rules.reload();show(id)}}/>:null}
 </section>
}

function RuleDetail({rule,onRefresh,onEdit,onBusyChange}:{rule:Rule;onRefresh:()=>void;onEdit:()=>void;onBusyChange:(busy:boolean)=>void}){
 const {endpoints}=useSession(),[confirmRetire,setConfirmRetire]=useState(false)
 const project=useResource(rule.scope.project_id?(signal)=>endpoints.getProject(rule.scope.project_id!,signal):null,[rule.scope.project_id])
 const task=useResource(rule.scope.task_id?(signal)=>endpoints.getTask(rule.scope.task_id!,signal):null,[rule.scope.task_id])
 const origin=useResource((signal)=>endpoints.getEvent(rule.origin_event_id,signal),[rule.origin_event_id])
 const hosts=useResource(signal=>endpoints.listHosts(signal),[]),[host,setHost]=useState('')
 const history=useResource((signal)=>endpoints.getRuleHistory(rule.rule_id,signal),[rule.rule_id,rule.revision])
 const approve=useWriteAction((body,signal)=>endpoints.approveRule(rule.rule_id,body,signal),{keyKind:'event_id',targetKey:rule.rule_id})
 const retire=useWriteAction((body,signal)=>endpoints.retireRule(rule.rule_id,body,signal),{keyKind:'event_id',targetKey:rule.rule_id})
 useEffect(()=>{if(approve.result||retire.result)onRefresh()},[approve.result,retire.result])
 const action=retire.state!=='idle'?retire:approve
 const hostId=host||hosts.data?.hosts[0]?.host_id||'',busy=approve.busy||retire.busy
 useEffect(()=>{onBusyChange(busy);return()=>onBusyChange(false)},[busy,onBusyChange])
 return <>
  <div className="rule-detail-tags"><Badge variant={rule.status==='ACTIVE'?'success':rule.status==='PROPOSED'?'warning':'outline'}>{RULE_STATES[rule.status]??rule.status}</Badge><span>{RULE_KINDS[rule.kind]??rule.kind}</span><span>{RULE_USES[rule.enforcement]??rule.enforcement}</span>{rule.severity==='HARD'?<span>强约束</span>:null}</div>
  <RawText value={rule.content} label="规则内容" appearance="chat" maxCollapsed={1200}/>
  <dl className="rule-guide-list"><dt>适用范围</dt><dd>{rule.scope.kind==='global'?'所有项目':project.data?.project.name??'指定项目'}{rule.scope.kind==='task'?` / ${task.data?.task.title??'指定任务'}`:rule.scope.kind==='project'?' / 整个项目':''}</dd><dt>使用方式</dt><dd>{rule.enforcement==='CONTEXT'?'供上下文构建使用；Agent 是否收到取决于注入接入。':rule.enforcement==='VERIFY'?'作为验收检查要求，不表示已经验证通过。':'由已接入 Guard 的 Agent 在工具执行前检查；批准本身不会开启拦截。'}</dd>{rule.enforcement!=='CONTEXT'?<><dt>匹配条件</dt><dd>{maskCredentials(ruleTarget(rule)).text}</dd></>:null}</dl>
  <details className="rule-source"><summary>查看原始依据</summary>{origin.data?<RawText value={String(origin.data.event.payload.text??'此来源为结构化事件，请查看来源记录。')} label={rule.origin_actor_kind==='human'?'用户原话':'来源内容'} appearance="chat"/>:origin.error?<ErrorPanel error={origin.error} what="来源内容" onRetry={origin.reload}/>:<p>正在读取来源…</p>}<a href={sourceHref.event(rule.origin_event_id)}>打开来源记录</a></details>
  <div className="processing-actions">{rule.status==='PROPOSED'?<Button disabled={busy||approve.state==='succeeded'||!hostId} onClick={()=>void approve.submit({host_id:hostId,expected_revision:rule.revision})}>批准这条规则</Button>:null}{rule.status==='ACTIVE'?<><Button variant="outline" disabled={busy} onClick={onEdit}>修改规则</Button><Button variant="ghost" disabled={busy} onClick={()=>setConfirmRetire(true)}>停用规则</Button></>:null}</div>
  {confirmRetire&&rule.status==='ACTIVE'?<div className="rules-note"><span>停用后这条规则不再生效，历史仍保留。</span><Button variant="outline" disabled={busy||!hostId} onClick={()=>void retire.submit({host_id:hostId,expected_revision:rule.revision})}>确认停用</Button><Button variant="ghost" disabled={busy} onClick={()=>setConfirmRetire(false)}>取消</Button></div>:null}
  {action.state==='conflict'?<p role="alert">规则已被其他操作更新。请刷新并重新核对，当前操作未自动重试。</p>:action.state==='refused'?<p role="alert">操作未获准，请核对当前凭证的规则管理权限和规则状态。</p>:action.state==='unknown'?<p role="alert">结果尚未确认，请先刷新核对，勿重复创建操作。</p>:action.state==='succeeded'?<p role="status">操作已提交，正在读取当前规则。</p>:null}
  <details><summary>变更历史{history.data?` · ${history.data.history.length} 条`:''}</summary>{history.error?<ErrorPanel error={history.error} what="变更历史" onRetry={history.reload}/>:null}<ol className="rule-history">{history.data?.history.map(h=><li key={h.change_event_id}><div><Badge variant="outline">{RULE_STATES[h.rule.status]??h.rule.status}</Badge><time>{formatTimestamp(h.changed_at)}</time></div><p>{rulePreview(h.rule)}</p></li>)}</ol></details>
  <details className="rules-technical"><summary>技术详情与审计</summary><label>记录操作的设备<select value={hostId} onChange={e=>setHost(e.target.value)}>{hosts.data?.hosts.map(h=><option key={h.host_id} value={h.host_id}>{h.display_name||'本机设备'}</option>)}</select></label>{hosts.error?<ErrorPanel error={hosts.error} what="设备信息" onRetry={hosts.reload}/>:null}<JsonBlock value={{rule_id:rule.rule_id,rule_key:rule.rule_key,version:rule.version,revision:rule.revision,scope:rule.scope,matcher:rule.matcher,verification_requirements:rule.verification_requirements}} label="规则配置"/><a href={sourceHref.event(rule.origin_event_id)}>来源事件</a>{rule.status_event_id?<a href={sourceHref.event(rule.status_event_id)}>状态变更事件</a>:null}{action.error?<JsonBlock value={{code:action.error.code,request_id:action.requestId,details:action.error.details}} label="操作结果详情"/>:null}</details>
 </>
}
