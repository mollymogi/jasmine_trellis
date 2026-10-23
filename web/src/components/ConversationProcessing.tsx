import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, Clock3, RefreshCw, Sparkles, TriangleAlert } from 'lucide-react'
import { useSession } from '../app/session'
import { sourceHref } from '../app/router'
import { Button } from './ui/button'
import { Badge } from './ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { RawText } from './RawText'
import { InterpreterSettings } from './InterpreterSettings'
import { AgentLogo } from './AgentLogo'
import type { Review } from '../api/types'

import { ProcessingRecords } from './ProcessingRecords'
import { canReviewConvention, CANDIDATE_LABELS, processingAgent, processingPreview, processingState as summary, type ProcessingItem as Item } from '../lib/processingPresentation'
import { statusLabel } from '../lib/statusLabels'
import { formatTimestamp } from '../lib/text'

type Page={items:Item[];count:number;counts:Record<string,number>;worker_configured:boolean;started_at:string|null;last_tick_at:string|null}
export function ConversationProcessing({projectId=null,sessionId=null,eventId=null,compact=false}:{projectId?:string|null;sessionId?:string|null;eventId?:string|null;compact?:boolean}){
 const {token,endpoints}=useSession();const [page,setPage]=useState<Page|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[offset,setOffset]=useState(0)
 const [detailItem,setDetailItem]=useState<Item|null>(null)
 const [selected,setSelected]=useState<Item|null>(null),[review,setReview]=useState<Review|null>(null)
 const loadKey=JSON.stringify([projectId,sessionId,eventId,offset,compact]);const currentLoadKey=useRef(loadKey);currentLoadKey.current=loadKey
 const headers=useCallback(()=>({...(token?{Authorization:`Bearer ${token}`}:{})}),[token])
 const load=useCallback(async(signal?:AbortSignal)=>{
  const query=new URLSearchParams({limit:compact?'5':'20',offset:String(offset)});if(projectId)query.set('project_id',projectId);if(sessionId)query.set('session_id',sessionId);if(eventId)query.set('event_id',eventId)
  try{const r=await fetch('/v1/conversation-processing?'+query,{headers:headers(),signal,cache:'no-store'});if(!r.ok)throw Error(r.status===403?'当前账号没有查看提炼记录的权限。':r.status===404?'当前服务尚未启用对话提炼。':'暂时无法读取提炼记录。');const value=await r.json();if(!Array.isArray(value.items))throw Error('暂时无法读取提炼记录，请稍后刷新。');if(!signal?.aborted&&currentLoadKey.current===loadKey){setPage(value);setError('')}}catch(e){if(!signal?.aborted&&currentLoadKey.current===loadKey)setError(e instanceof Error?e.message:'读取失败。')}
 },[projectId,sessionId,eventId,offset,compact,headers,loadKey])
 useEffect(()=>{setOffset(0);setPage(null);setNotice('');setError('');setSelected(null);setReview(null);setDetailItem(null)},[projectId,sessionId,eventId])
 useEffect(()=>{setDetailItem(current=>current?(page?.items.find(item=>item.event_id===current.event_id)??current):null)},[page])
 useEffect(()=>{const abort=new AbortController();void load(abort.signal);const timer=setInterval(()=>void load(abort.signal),3000);return()=>{abort.abort();clearInterval(timer)}},[load])
 async function action(path:string,body:object){setBusy(true);setError('');try{const r=await fetch(path,{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error(r.status===403?'当前账号没有此操作权限。':'操作未完成，请刷新状态后重试。');const value=await r.json();setNotice(typeof value.queued==='number'?`已加入 ${value.queued} 条消息；已有处理不会重复。`:'已请求重试。');await load()}catch(e){setError(e instanceof Error?e.message:'操作失败。')}finally{setBusy(false)}}
 async function openReview(item:Item){setDetailItem(null);setSelected(item);setReview(null);setError('');try{const result=await endpoints.getReview(item.review_id!);setReview(result.review)}catch{setError('无法加载当前审核计划，请刷新后重试。')}}
 async function approve(){if(!selected||!review?.preview)return;setBusy(true);setError('');try{const p=review.preview;await endpoints.approveReview(review.review_id,{idempotency_key:crypto.randomUUID(),host_id:selected.host_id,reason:'确认此项目约定，按当前候选应用',expected_revision:review.revision,expected_revisions:p.expected_revisions,expected_context_digest:p.expected_context_digest,actions:p.plan.items.map(item=>({candidate_index:item.candidate_index,action:'CREATE_RULE',payload:{kind:item.candidate.kind==='DECISION'?'DECISION':'RULE',severity:'NORMAL',enforcement:'CONTEXT',content:item.candidate.content,matcher:{}}}))});setSelected(null);setReview(null);await load()}catch{setError('批准未完成，可能有更新或权限不足。请重新读取当前审核计划。')}finally{setBusy(false)}}
 const configured=page?.worker_configured&&page.started_at
 const available=configured&&!!page?.last_tick_at&&Date.now()-Date.parse(page.last_tick_at)<300000
 const content=<>
  <div className="processing-heading"><div><h2><Sparkles size={17}/>对话提炼</h2><p>{!page?'正在读取提炼记录…':available?'从对话中找出值得记住的内容，需要确认的会提醒你审核。':configured?'自动分析暂未响应，已收录的消息和处理记录仍保留。':'自动分析尚未启动，原对话仍正常收录。'}</p></div><div className="processing-actions"><Button variant="outline" size="sm" onClick={()=>void load()}><RefreshCw size={14}/>刷新</Button>{(eventId||sessionId||projectId)?<Button size="sm" disabled={busy||!page?.worker_configured} onClick={()=>void action('/v1/conversation-processing/enqueue',eventId?{event_id:eventId}:sessionId?{session_id:sessionId}:{project_id:projectId})}>分析{eventId?'此消息':sessionId?'本次会话':'项目历史'}</Button>:null}</div></div>
  {!compact?<div className="processing-actions"><InterpreterSettings/>{page?<p className="processing-count">共 {page.count} 条提炼记录</p>:null}</div>:null}
  {notice?<p role="status" className="processing-notice">{notice}</p>:null}{error?<p role="alert" className="processing-error"><TriangleAlert size={14}/>{error}</p>:null}
  {!page&&!error?<p className="muted">正在读取处理进度…</p>:null}
  {page?.items.length===0?<p className="processing-empty">还没有提炼记录。新对话会自动分析，也可以从对话页选择已有会话。</p>:null}
  {!compact&&page?.items.length?<ProcessingRecords items={page.items} offset={offset} onDetails={setDetailItem}/>:null}
  {compact?<div className="processing-cards">{page?.items.map(item=>{const state=summary(item),canApprove=canReviewConvention(item);return <Card key={item.event_id} className="processing-card"><CardHeader><div className="processing-card-top"><Badge variant={state.tone}>{state.label==='已入库'?<CheckCircle2 size={12}/>:<Clock3 size={12}/>} {state.label}</Badge><span className="processing-source" title={item.project_name||'未命名项目'}><AgentLogo agent={item.source_system.split('-native')[0]??'unknown'} size={16}/>{item.project_name??item.source_system.split('-native')[0]}</span></div><CardTitle>{processingPreview(item)}</CardTitle><CardDescription>{state.description}</CardDescription></CardHeader><CardContent>
   <div className="processing-actions">{canApprove?<Button size="sm" onClick={()=>void openReview(item)}>审核项目约定</Button>:null}{item.review_id?<Button asChild size="sm" variant="outline"><a href={sourceHref.review(item.review_id)}>查看审核</a></Button>:null}{item.status==='FAILED'?<Button variant="outline" size="sm" disabled={busy} onClick={()=>void action('/v1/conversation-processing/'+item.event_id+'/retry',{})}>重新分析</Button>:null}{item.memory_items.map(memory=><Button asChild key={memory.memory_id} size="sm" variant="outline"><a href={'#/memory/items/'+memory.memory_id}>查看记忆</a></Button>)}</div>
   <details><summary>来源与处理细节</summary><div className="processing-details"><RawText value={item.source_text} label="原始消息"/><a href={sourceHref.event(item.event_id)}>查看原始消息</a>{item.interpretation_id?<a href={sourceHref.interpretation(item.interpretation_id)}>查看提炼结果</a>:null}{item.resolution_id?<a href={sourceHref.resolution(item.resolution_id)}>查看策略决议</a>:null}{item.memory_jobs.map(job=><a href={'#/memory/jobs/'+job.job_id} key={job.job_id}>写入任务：{statusLabel('job',job.status)}</a>)}{item.error_code?<code>{item.error_code}</code>:null}</div></details>
  </CardContent></Card>})}</div>:null}
  {!compact&&page&&page.count>20?<div className="processing-pagination"><Button variant="outline" size="sm" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页</Button><span>{offset+1}–{Math.min(offset+20,page.count)} / {page.count}</span><Button variant="outline" size="sm" disabled={offset+20>=page.count} onClick={()=>setOffset(offset+20)}>下一页</Button></div>:null}
 </>
 return <section className={compact?'processing-panel processing-panel--compact':'processing-panel'} aria-label="对话到记忆处理进度">{compact?<details><summary>对话到记忆 · {page?`${page.count} 条处理记录`:'读取进度'}</summary>{content}<a href="#/interpretations">查看全部处理进度</a></details>:content}
 <Dialog open={!!detailItem} onOpenChange={open=>{if(!open)setDetailItem(null)}}><DialogContent className="processing-detail-dialog"><DialogHeader><DialogTitle>提炼详情</DialogTitle><DialogDescription>查看这条内容的来源和处理结果。</DialogDescription></DialogHeader>{detailItem?<>
    <div className="processing-detail-meta"><span className="processing-agent"><AgentLogo agent={processingAgent(detailItem).id} size={22}/>{processingAgent(detailItem).name}</span><span>{detailItem.project_name||'未命名项目'}</span><time>{formatTimestamp(detailItem.queued_at)}</time><Badge variant={summary(detailItem).tone}>{summary(detailItem).label}</Badge></div>
    <p className="processing-detail-description">{summary(detailItem).description}</p>
    <RawText value={detailItem.source_text} label="对话原文"/>
    {detailItem.candidates.filter(c=>c.kind!=='NO_STRUCTURE').map((candidate,index)=><RawText key={index} value={candidate.content} label={CANDIDATE_LABELS[candidate.kind]??'提炼内容'}/>)}
    <div className="processing-actions">{canReviewConvention(detailItem)?<Button onClick={()=>void openReview(detailItem)}>审核项目约定</Button>:null}{detailItem.status==='FAILED'?<Button disabled={busy} onClick={()=>void action('/v1/conversation-processing/'+detailItem.event_id+'/retry',{})}>重新分析</Button>:null}{detailItem.review_id&&!canReviewConvention(detailItem)&&detailItem.status==='PENDING_REVIEW'?<Button asChild><a href={sourceHref.review(detailItem.review_id)}>前往审核</a></Button>:null}{detailItem.memory_items.map(memory=><Button asChild key={memory.memory_id} variant="outline"><a href={'#/memory/items/'+memory.memory_id}>查看记忆</a></Button>)}</div>
    <details className="processing-technical"><summary>技术详情</summary><div className="processing-details"><p>原始状态：<code>{detailItem.status}</code></p><p>消息编号：<code>{detailItem.event_id}</code></p>{detailItem.interpretation_id?<p>提取编号：<code>{detailItem.interpretation_id}</code></p>:null}{detailItem.extractor_model?<p>提取模型：{detailItem.extractor_model}</p>:null}<a href={sourceHref.event(detailItem.event_id)}>查看原始消息</a>{detailItem.interpretation_id?<a href={sourceHref.interpretation(detailItem.interpretation_id)}>查看提炼结果</a>:null}{detailItem.resolution_id?<a href={sourceHref.resolution(detailItem.resolution_id)}>查看策略决议</a>:null}{detailItem.review_id?<a href={sourceHref.review(detailItem.review_id)}>查看审核记录</a>:null}{detailItem.memory_jobs.map(job=><a key={job.job_id} href={'#/memory/jobs/'+job.job_id}>写入任务：{statusLabel('job',job.status)}</a>)}{detailItem.error_code?<code>{detailItem.error_code}</code>:null}</div></details>
  </>:null}</DialogContent></Dialog>
 <Dialog open={!!selected} onOpenChange={open=>{if(!open){setSelected(null);setReview(null)}}}><DialogContent><DialogHeader><DialogTitle>确认项目约定</DialogTitle><DialogDescription>请核对原话和适用范围。批准后会保留为本项目约定，并保存为记忆。</DialogDescription></DialogHeader>{selected?.candidates.some(c=>c.impact==='HIGH')?<p className="processing-notice" role="status">此候选被标记为高影响，请仔细核对。这里仅批准为本项目的上下文约定。</p>:null}{selected?.candidates.map((c,i)=><div key={i}><RawText value={c.content} label="拟保存的项目约定"/><details><summary>查看原话依据</summary><RawText value={c.source_span.quote} label="来源原话"/></details></div>)}{error?<p role="alert">{error}</p>:null}<div className="processing-actions"><Button disabled={busy||!review?.preview||review.status!=='PENDING'} onClick={()=>void approve()}>{busy?'正在批准…':'批准此项目约定'}</Button><Button variant="outline" onClick={()=>setSelected(null)}>暂不处理</Button></div></DialogContent></Dialog>
 </section>
}
