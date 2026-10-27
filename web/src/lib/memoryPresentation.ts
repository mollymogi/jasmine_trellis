import type { MemoryItem } from '../api/types'
import { clip, maskCredentials } from './text'
export const MEMORY_TYPES: Record<string, string> = {
  CONFIRMED_DECISION: '已确认的决定', STABLE_PROJECT_FACT: '项目约定与事实',
  RESOLVED_ISSUE: '已解决的问题', VERIFIED_OUTCOME: '有验证依据的结果', FAILURE_LESSON: '失败经验',
  OBSERVED_CONVENTION: '对话中的约定', OBSERVED_DECISION: '对话中的决定',
  OBSERVED_CORRECTION: '对话中的纠正', OBSERVED_ACCEPTANCE: '对话中的验收',
}
export const MEMORY_STATES: Record<string, string> = { ACTIVE: '保留中', OBSOLETE: '已过期', DELETED: '已删除' }
export const MEMORY_ENGINE_STATES: Record<string, string> = {
  RETAINED: '已保存至引擎', PENDING_RETAIN: '等待写入', RETAIN_FAILED: '写入失败',
  DELETE_PENDING: '等待从引擎删除', DELETED: '已从引擎删除', DELETE_FAILED: '删除失败',
}
export function memoryPreview(item: MemoryItem) {
  return clip(maskCredentials(item.sanitized_text ?? '').text.replace(/\s+/g, ' ').trim(), 160) || '此条目没有可显示的文本'
}
export function memoryType(type: string) { return MEMORY_TYPES[type] ?? '其他记忆类型' }
export function memoryState(item: MemoryItem) {
  return MEMORY_STATES[item.status] ?? '生命周期待确认'
}
export function memoryEngineState(item: MemoryItem) {
  return MEMORY_ENGINE_STATES[item.engine_state] ?? '引擎状态待确认'
}
