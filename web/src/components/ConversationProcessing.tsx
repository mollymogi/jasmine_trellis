import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, Clock3, RefreshCw, Sparkles, TriangleAlert } from 'lucide-react'
import { useSession } from '../app/session'
import { sourceHref } from '../app/router'
import { maskedSummary } from '../lib/text'
import { Button } from './ui/button'
import { Badge } from './ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { RawText } from './RawText'
import { AgentLogo } from './AgentLogo'
import type { InterpretationCandidate, Review } from '../api/types'

type Item={event_id:string;status:string;source_text:string;source_actor_kind:string;host_id:string;project_id:string;project_name?:string|null;session_id:string|null;event_type:string;source_system:string;interpretation_id:string|null;resolution_id:string|null;review_id:string|null;error_code:string|null;effect:string;candidates:InterpretationCandidate[];memory_items:{memory_id:string;memory_type:string;engine_state:string;status:string}[];memory_jobs:{job_id:string;status:string;last_error_code:string|null}[]}
type Page={items:Item[];count:number;counts:Record<string,number>;worker_configured:boolean;started_at:string|null;last_tick_at:string|null}
function summary(item:Item){
 if(item.memory_items.some(m=>m.engine_state==='RETAINED'&&m.status==='ACTIVE'))return {label:'已入库',description:'记忆已写入，可查看来源和当前规则。',tone:'success' as const}
 if(item.memory_items.length>0&&item.memory_items.every(m=>m.status!=='ACTIVE'))return {label:'历史记忆',description:'这条记忆已失效或删除，不再用于当前召回。',tone:'outline' as const}
 if(item.memory_jobs.some(j=>j.status==='FAILED'))return {label:'写入失败',description:'解释和决议已保留，记忆写入需要处理。',tone:'warning' as const}
 if(item.status==='PENDING_REVIEW')return {label:'待审核',description:'候选尚未生效。审核后才会继续处理。',tone:'warning' as const}
 if(item.status==='FAILED')return {label:'分析失败',description:'原对话已保留，可查看原因后重试。',tone:'warning' as const}
 if(item.status==='REJECTED')return {label:'已忽略',description:'审核已拒绝，原对话仍保留。',tone:'outline' as const}
 if(item.status==='NO_ACTION')return {label:'无需记忆',description:'本条没有可应用的结构，不写入长期记忆。',tone:'outline' as const}
 if(item.status==='APPLIED')return item.effect==='MEMORY_ELIGIBLE'?{label:'等待入库',description:'合格来源已提交，Memory worker 将异步处理。',tone:'outline' as const}:{label:'已处理',description:item.effect==='TASK_ONLY'?'已形成当前任务要求；没有长期记忆来源。':'处理完成，没有新的长期记忆。',tone:'outline' as const}
 return {label:item.status==='PROCESSING'?'分析中':'等待分析',description:'正在提取含义并按策略判断。',tone:'outline' as const}
}
export function ConversationProcessing({projectId=null,sessionId=null,eventId=null,compact=false}:{projectId?:string|null;sessionId?:string|null;eventId?:string|null;compact?:boolean}){
 const {token,endpoints}=useSession();const [page,setPage]=useState<Page|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[offset,setOffset]=useState(0)
 const [selected,setSelected]=useState<Item|null>(null),[review,setReview]=useState<Review|null>(null)
 const loadKey=JSON.stringify([projectId,sessionId,eventId,offset,compact]);const currentLoadKey=useRef(loadKey);currentLoadKey.current=loadKey
 const headers=useCallback(()=>({...(token?{Authorization:`Bearer ${token}`}:{})}),[token])
 const load=useCallback(async(signal?:AbortSignal)=>{
  const query=new URLSearchParams({limit:compact?'5':'20',offset:String(offset)});if(projectId)query.set('project_id',projectId);if(sessionId)query.set('session_id',sessionId);if(eventId)query.set('event_id',eventId)
  try{const r=await fetch('/v1/conversation-processing?'+query,{headers:headers(),signal,cache:'no-store'});if(!r.ok)throw Error(r.status===403?'当前凭证不能查看完整处理链路。':r.status===404?'当前 Core 尚未启用对话处理链路。':'处理进度暂时不可用。');const value=await r.json();if(!Array.isArray(value.items))throw Error('处理服务返回了无效结果。');if(!signal?.aborted&&currentLoadKey.current===loadKey){setPage(value);setError('')}}catch(e){if(!signal?.aborted&&currentLoadKey.current===loadKey)setError(e instanceof Error?e.message:'读取失败。')}
 },[projectId,sessionId,eventId,offset,compact,headers,loadKey])
 useEffect(()=>{setOffset(0);setPage(null);setNotice('');setError('');setSelected(null);setReview(null)},[projectId,sessionId,eventId])
 useEffect(()=>{const abort=new AbortController();void load(abort.signal);const timer=setInterval(()=>void load(abort.signal),3000);return()=>{abort.abort();clearInterval(timer)}},[load])
 async function action(path:string,body:object){setBusy(true);setError('');try{const r=await fetch(path,{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error(r.status===403?'当前凭证没有处理权限。':'操作未完成，请刷新状态后重试。');const value=await r.json();setNotice(typeof value.queued==='number'?`已加入 ${value.queued} 条消息；已有处理不会重复。`:'已请求重试。');await load()}catch(e){setError(e instanceof Error?e.message:'操作失败。')}finally{setBusy(false)}}
 async function openReview(item:Item){setSelected(item);setReview(null);setError('');try{const result=await endpoints.getReview(item.review_id!);setReview(result.review)}catch{setError('无法加载当前审核计划，请刷新后重试。')}}
 async function approve(){if(!selected||!review?.preview)return;setBusy(true);setError('');try{const p=review.preview;await endpoints.approveReview(review.review_id,{idempotency_key:crypto.randomUUID(),host_id:selected.host_id,reason:'确认此项目约定，按当前候选应用',expected_revision:review.revision,expected_revisions:p.expected_revisions,expected_context_digest:p.expected_context_digest,actions:p.plan.items.map(item=>({candidate_index:item.candidate_index,action:'CREATE_RULE',payload:{kind:item.candidate.kind==='DECISION'?'DECISION':'RULE',severity:'NORMAL',enforcement:'CONTEXT',content:item.candidate.content,matcher:{}}}))});setSelected(null);setReview(null);await load()}catch{setError('批准未完成，可能有更新或权限不足。请重新读取当前审核计划。')}finally{setBusy(false)}}
 const configured=page?.worker_configured&&page.started_at
 const available=configured&&!!page?.last_tick_at&&Date.now()-Date.parse(page.last_tick_at)<300000
 const content=<>
  <div className="processing-heading"><div><h2><Sparkles size={17}/>对话到记忆</h2><p>{available?'新用户消息自动分析，按策略生效或进入审核；稳定来源异步入库。':configured?'自动分析暂未响应，已收录的消息和处理记录仍保留。':'自动分析尚未启动，原对话仍正常收录。'}</p></div><div className="processing-actions"><Button variant="outline" size="sm" onClick={()=>void load()}><RefreshCw size={14}/>刷新</Button>{(eventId||sessionId||projectId)?<Button size="sm" disabled={busy||!page?.worker_configured} onClick={()=>void action('/v1/conversation-processing/enqueue',eventId?{event_id:eventId}:sessionId?{session_id:sessionId}:{project_id:projectId})}>分析{eventId?'此消息':sessionId?'本次会话':'项目历史'}</Button>:null}</div></div>
  <div className="processing-flow"><span>对话收录</span><ArrowRight size={12}/><span>理解含义</span><ArrowRight size={12}/><span>策略与审核</span><ArrowRight size={12}/><span>长期记忆</span></div>
  {notice?<p role="status" className="processing-notice">{notice}</p>:null}{error?<p role="alert" className="processing-error"><TriangleAlert size={14}/>{error}</p>:null}
  {!page&&!error?<p className="muted">正在读取处理进度…</p>:null}
  {page?.items.length===0?<p className="processing-empty">尚无分析记录。新消息会自动进入；可在 Conversation 选中会话，分析已收录的历史消息。</p>:null}
  <div className="processing-cards">{page?.items.map(item=>{const state=summary(item),canApprove=item.status==='PENDING_REVIEW'&&item.source_actor_kind==='human'&&item.event_type==='user.prompt'&&item.candidates.length>0&&item.candidates.every(c=>['RULE','DECISION'].includes(c.kind)&&c.scope.kind==='PROJECT'&&c.certainty==='EXPLICIT');return <Card key={item.event_id} className="processing-card"><CardHeader><div className="processing-card-top"><Badge variant={state.tone}>{state.label==='已入库'?<CheckCircle2 size={12}/>:<Clock3 size={12}/>} {state.label}</Badge><span className="processing-source" title={item.project_name??item.project_id}><AgentLogo agent={item.source_system.split('-native')[0]??'unknown'} size={16}/>{item.project_name??item.source_system.split('-native')[0]}</span></div><CardTitle>{maskedSummary(item.candidates.find(c=>c.kind!=='NO_STRUCTURE')?.content??item.source_text)}</CardTitle><CardDescription>{state.description}</CardDescription></CardHeader><CardContent>
   <div className="processing-actions">{canApprove?<Button size="sm" onClick={()=>void openReview(item)}>审核项目约定</Button>:null}{item.review_id?<Button asChild size="sm" variant="outline"><a href={sourceHref.review(item.review_id)}>查看审核</a></Button>:null}{item.status==='FAILED'?<Button variant="outline" size="sm" disabled={busy} onClick={()=>void action('/v1/conversation-processing/'+item.event_id+'/retry',{})}>重新分析</Button>:null}{item.memory_items.map(memory=><Button asChild key={memory.memory_id} size="sm" variant="outline"><a href={'#/memory/items/'+memory.memory_id}>查看记忆</a></Button>)}</div>
   <details><summary>来源与处理细节</summary><div className="processing-details"><RawText value={item.source_text} label="原始消息"/><a href={sourceHref.event(item.event_id)}>查看原始 Event</a>{item.interpretation_id?<a href={sourceHref.interpretation(item.interpretation_id)}>查看解释候选</a>:null}{item.resolution_id?<a href={sourceHref.resolution(item.resolution_id)}>查看策略决议</a>:null}{item.memory_jobs.map(job=><a href={'#/memory/jobs/'+job.job_id} key={job.job_id}>写入队列：{job.status}</a>)}{item.error_code?<code>{item.error_code}</code>:null}</div></details>
  </CardContent></Card>})}</div>
  {!compact&&page&&page.count>20?<div className="processing-pagination"><Button variant="outline" size="sm" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页</Button><span>{offset+1}–{Math.min(offset+20,page.count)} / {page.count}</span><Button variant="outline" size="sm" disabled={offset+20>=page.count} onClick={()=>setOffset(offset+20)}>下一页</Button></div>:null}
 </>
 return <section className={compact?'processing-panel processing-panel--compact':'processing-panel'} aria-label="对话到记忆处理进度">{compact?<details><summary>对话到记忆 · {page?`${page.count} 条处理记录`:'读取进度'}</summary>{content}<a href="#/interpretations">查看全部处理进度</a></details>:content}
 <Dialog open={!!selected} onOpenChange={open=>{if(!open){setSelected(null);setReview(null)}}}><DialogContent><DialogHeader><DialogTitle>确认项目约定</DialogTitle><DialogDescription>请核对原话和适用范围。批准后会成为本项目的上下文约定，并交给 Memory 异步保存。</DialogDescription></DialogHeader>{selected?.candidates.some(c=>c.impact==='HIGH')?<p className="processing-notice" role="status">此候选被标记为高影响，请仔细核对。这里仅批准为本项目的上下文约定。</p>:null}{selected?.candidates.map((c,i)=><div key={i}><RawText value={c.content} label="拟保存的项目约定"/><details><summary>查看原话依据</summary><RawText value={c.source_span.quote} label="来源原话"/></details></div>)}{error?<p role="alert">{error}</p>:null}<div className="processing-actions"><Button disabled={busy||!review?.preview||review.status!=='PENDING'} onClick={()=>void approve()}>{busy?'正在批准…':'批准此项目约定'}</Button><Button variant="outline" onClick={()=>setSelected(null)}>暂不处理</Button></div></DialogContent></Dialog>
 </section>
}
