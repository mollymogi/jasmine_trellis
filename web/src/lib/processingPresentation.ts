import type { InterpretationCandidate } from '../api/types'
import { maskedSummary } from './text'

export type ProcessingItem = {
  event_id: string; status: string; source_text: string; source_actor_kind: string; host_id: string;
  project_id: string; project_name?: string|null; session_id: string|null; event_type: string;
  source_system: string; interpretation_id: string|null; resolution_id: string|null; review_id: string|null;
  queued_at?: string; error_code: string|null; effect: string; extractor_model?: string;
  candidates: InterpretationCandidate[];
  memory_items: {memory_id:string;memory_type:string;engine_state:string;status:string}[];
  memory_jobs: {job_id:string;status:string;last_error_code:string|null}[];
}
export function processingState(item: ProcessingItem) {
  if(item.memory_items.some(m=>m.engine_state==='RETAINED'&&m.status==='ACTIVE'))return {label:'已入库',description:'这条记忆已保存，可以查看内容和来源。',tone:'success' as const}
  if(item.memory_items.length>0&&item.memory_items.every(m=>m.status!=='ACTIVE'))return {label:'历史记忆',description:'这条记忆已失效或删除，不再用于当前召回。',tone:'outline' as const}
  if(item.memory_jobs.some(j=>j.status==='FAILED'))return {label:'写入失败',description:'提炼结果已保留，记忆保存未完成。',tone:'warning' as const}
  if(item.status==='PENDING_REVIEW')return {label:'待审核',description:'请确认这条内容是否适合作为项目约定。',tone:'warning' as const}
  if(item.status==='FAILED')return {label:'分析失败',description:'原对话已保留，可以重新分析。',tone:'warning' as const}
  if(item.status==='REJECTED')return {label:'已忽略',description:'审核未通过，原对话仍然保留。',tone:'outline' as const}
  if(item.status==='NO_ACTION')return {label:'无需记忆',description:'普通聊天或临时内容，无需保存为长期记忆。',tone:'outline' as const}
  if(item.status==='APPLIED')return item.effect==='MEMORY_ELIGIBLE'
    ? {label:'等待入库',description:'已经确认，正在等待保存为记忆。',tone:'outline' as const}
    : {label:'已处理',description:item.effect==='TASK_ONLY'?'已更新任务要求，无需保存为长期记忆。':'已处理完成，没有新增长期记忆。',tone:'outline' as const}
  if(item.status==='PROCESSING')return {label:'分析中',description:'正在理解这条消息。',tone:'outline' as const}
  if(item.status==='PENDING')return {label:'等待分析',description:'消息已收录，正在等待分析。',tone:'outline' as const}
  return {label:'状态待确认',description:'暂时无法确认处理结果，请查看详情。',tone:'warning' as const}
}
const AGENTS: Record<string,string>={codex:'Codex',claude:'Claude',grok:'Grok'}
export function processingAgent(item:ProcessingItem) {
  const id=item.source_system.split('-native')[0]??'unknown'
  return {id,name:AGENTS[id]??'未识别来源'}
}
export function processingPreview(item:ProcessingItem) {
  const candidate=item.candidates.find(c=>c.kind!=='NO_STRUCTURE')?.content
  // Only the display preview discards the known browser wrapper. Stored text,
  // quoted evidence and server-owned provenance are unchanged.
  const source=item.source_text.replace(/^\s*<in-app-browser-context\b[\s\S]*?<\/in-app-browser-context>\s*(?:## My request:\s*)?/,'')
  const preview=maskedSummary(candidate??source)
  return (preview.length>110?preview.slice(0,110)+'…':preview)||'暂无内容摘要'
}
export function processingTime(value?:string) {
  if(!value||Number.isNaN(new Date(value).getTime()))return '—'
  return new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value))
}
export function canReviewConvention(item:ProcessingItem) {
  return item.status==='PENDING_REVIEW'&&item.source_actor_kind==='human'&&item.event_type==='user.prompt'&&item.candidates.length>0&&item.candidates.every(c=>['RULE','DECISION'].includes(c.kind)&&c.scope.kind==='PROJECT'&&c.certainty==='EXPLICIT')
}
export const CANDIDATE_LABELS:Record<string,string>={RULE:'项目约定',DECISION:'项目决定',TASK_CREATE_OR_ATTACH:'任务安排',REQUIRED_CAPABILITY:'能力要求',CORRECTION:'修正要求',ACCEPTANCE:'验收要求',NO_STRUCTURE:'无需长期记忆'}
