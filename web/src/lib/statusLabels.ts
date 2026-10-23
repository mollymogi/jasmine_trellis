/** Presentation labels only. Core values, request bodies and data-status stay unchanged. */
const COMMON: Record<string, string> = {
  PLANNED: '待执行', IN_PROGRESS: '执行中', EXECUTED: '已执行', VERIFIED: '已验证',
  ACCEPTED: '已验收', FAILED: '失败', BLOCKED: '受阻', STALE: '已失效', SKIPPED: '已跳过',
  ACTIVE: '进行中', CANCELLED: '已取消', PASS: '通过', FAIL: '未通过', INFO: '仅供参考',
  PROCESSING: '分析中', EXTRACTED: '已提取', INTERRUPTED: '已中断',
  APPLIED: '已生效', PENDING_REVIEW: '待审核', NO_ACTION: '无需处理',
  REJECTED: '已拒绝', SOURCE_REPLAYED: '已处理过', PENDING: '待审核', APPROVED: '已批准',
  PROPOSED: '待生效', SUPERSEDED: '已被替代', RETIRED: '已停用', open: '进行中', ended: '已结束',
  OK: '正常', NONE: '无记录', BROKEN_REFERENCE: '引用失效',
  CREATE_RULE: '创建规则', ATTACH_TASK: '关联任务', CREATE_TASK: '创建任务',
  ok: '正常', corrupt: '内容损坏', duplicate: '重复记录', frozen: '已固化', absent: '未提供',
  match: '校验一致', mismatch: '校验不一致', missing: '文件缺失', symlink_refused: '链接不可用',
  unreadable: '无法读取', attributed: '已归属项目', unattributed: '项目未明',
  real: '真实记录', component: '组件验证', simulated: '模拟记录', unknown: '尚未确认', synthetic: '合成记录',
}
const SPECIFIC: Record<string, Record<string, string>> = {
  rule: { ACTIVE: '已生效' },
  interpretation: { FAILED: '提取失败' },
  job: { PENDING: '等待处理', RUNNING: '处理中', DONE: '已完成', FAILED: '处理失败' },
  memory: { ACTIVE: '可用', OBSOLETE: '已失效', DELETED: '已删除', RETAINED: '已入库' },
}
export function statusLabel(kind: string, status: string): string {
  return SPECIFIC[kind]?.[status] ?? COMMON[status] ?? status
}
