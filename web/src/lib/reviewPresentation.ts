import type { RawEvent, Review } from '../api/types'
import { CANDIDATE_LABELS } from './processingPresentation'
import { clip, maskedSummary } from './text'

const REASONS: Record<string, string> = {
  not_explicit: '原话未明确表达，需要你确认',
  source_not_user_explicit: '来源不是用户明确提出的要求',
  scope_or_impact_requires_review: '涉及适用范围或较大影响',
  manual_plan_required: '需要明确具体的处理方案',
  task_binding_required: '需要先确定所属任务',
  explicit_task_binding: '包含任务安排',
  task_additive_normal_context: '包含任务约定',
  no_structure: '无需结构化处理',
}
export const reviewItems = (review: Review) => (review.preview?.plan ?? review.resolution.plan).items
export function reviewSummary(review: Review): string {
  const contents = reviewItems(review).map(item => item.candidate.content).filter(Boolean)
  return clip(maskedSummary(contents.join('；') || '当前计划内容不可用，请查看详情'), 140)
}
export function reviewReason(review: Review): string {
  const reasons = review.resolution.result.reasons
  return [...new Set(reasons.map(reason => REASONS[reason] ?? '需要人工核对处理方式'))].join('；') || '请核对内容和适用范围'
}
export function reviewKind(kind: string): string { return CANDIDATE_LABELS[kind] ?? '其他建议' }
export function reviewAgent(event?: RawEvent | null): { id: string; name: string } {
  const source = event?.source_system ?? ''
  const id = ['codex', 'claude', 'grok', 'opencode', 'cursor', 'gemini'].find(name => source === name || source.startsWith(name + '-'))
  return id ? { id, name: { codex: 'Codex', claude: 'Claude', grok: 'Grok', opencode: 'OpenCode', cursor: 'Cursor', gemini: 'Gemini' }[id]! } : { id: 'unknown', name: event ? '其他来源' : '来源待确认' }
}
/** Same narrow project-convention operation as conversation processing. Never turn an inferred task into a rule. */
export function conventionActions(review: Review, source: RawEvent | null | undefined) {
  const items = review.preview?.plan.items ?? []
  if (review.status !== 'PENDING' || !source || source.actor_kind !== 'human' || source.event_type !== 'user.prompt' || !source.project_id || !items.length) return null
  if (!items.every(({ candidate: c }) => ['RULE', 'DECISION'].includes(c.kind) && c.certainty === 'EXPLICIT' && c.scope.kind === 'PROJECT' && c.scope.project_id === source.project_id)) return null
  return items.map(item => ({ candidate_index: item.candidate_index, action: 'CREATE_RULE', payload: { kind: item.candidate.kind === 'DECISION' ? 'DECISION' : 'RULE', severity: 'NORMAL', enforcement: 'CONTEXT', content: item.candidate.content, matcher: {} } }))
}
