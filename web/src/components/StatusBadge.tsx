/**
 * Status badges.
 *
 * Every status carries an icon and its literal Core token as text, so the value
 * is readable without colour perception, in a screenshot and in a terminal
 * browser. Unknown vocabulary is *not* mapped onto a known tone: it renders as
 * `? <VALUE>` with the "unknown" style (P6 negative case "未知 enum 原样醒目展示").
 */

import { Badge, type badgeVariants } from '@/components/ui/badge'
import type { LucideIcon } from 'lucide-react'
import {
  ArrowRight,
  Circle,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleHelp,
  CircleSlash,
  CircleX,
  Hash,
  Info,
  OctagonX,
  Play,
  RotateCcw,
  Rows3,
  Snowflake,
  Square,
  SquareStack,
  Star,
  TriangleAlert,
} from 'lucide-react'
import { cls } from '../lib/classNames'
import type { VariantProps } from 'class-variance-authority'

export type BadgeKind =
  | 'task'
  | 'step'
  | 'evidence'
  | 'reference'
  | 'rule'
  | 'session'
  | 'interpretation'
  | 'disposition'
  | 'review'
  | 'action'
  | 'validation'

type Tone = 'neutral' | 'progress' | 'good' | 'warn' | 'bad' | 'unknown'

interface ToneSpec {
  icon: LucideIcon
  tone: Tone
  label?: string
}

const STEP: Record<string, ToneSpec> = {
  PLANNED: { icon: Circle, tone: 'neutral' },
  IN_PROGRESS: { icon: CircleDot, tone: 'progress' },
  EXECUTED: { icon: CircleDot, tone: 'progress' },
  VERIFIED: { icon: CircleCheck, tone: 'good' },
  ACCEPTED: { icon: Star, tone: 'good' },
  FAILED: { icon: CircleX, tone: 'bad' },
  BLOCKED: { icon: OctagonX, tone: 'bad' },
  STALE: { icon: TriangleAlert, tone: 'warn' },
  SKIPPED: { icon: CircleSlash, tone: 'neutral' },
}

const TASK: Record<string, ToneSpec> = {
  ACTIVE: { icon: Play, tone: 'progress' },
  BLOCKED: { icon: OctagonX, tone: 'bad' },
  ACCEPTED: { icon: Star, tone: 'good' },
  CANCELLED: { icon: CircleSlash, tone: 'neutral' },
}

const EVIDENCE: Record<string, ToneSpec> = {
  PASS: { icon: CircleCheck, tone: 'good' },
  FAIL: { icon: CircleX, tone: 'bad' },
  INFO: { icon: Info, tone: 'warn', label: 'INFO 不是 PASS' },
}

const REFERENCE: Record<string, ToneSpec> = {
  OK: { icon: CircleCheck, tone: 'good' },
  NONE: { icon: Circle, tone: 'neutral', label: '无引用可校验' },
  BROKEN_REFERENCE: { icon: TriangleAlert, tone: 'bad', label: '引用已失效,不能满足 criteria' },
}

const RULE: Record<string, ToneSpec> = {
  ACTIVE: { icon: CircleCheck, tone: 'good' },
  PROPOSED: { icon: CircleDashed, tone: 'warn', label: '已提议,未生效' },
  SUPERSEDED: { icon: ArrowRight, tone: 'neutral' },
  RETIRED: { icon: CircleSlash, tone: 'neutral' },
}

const SESSION: Record<string, ToneSpec> = {
  open: { icon: Play, tone: 'progress' },
  ended: { icon: Square, tone: 'neutral' },
}

// P2 vocabularies, copied from the frozen enums: interpretations.STATUSES
// (PROCESSING/EXTRACTED/FAILED/INTERRUPTED), the resolution dispositions the
// policy and review paths emit, `reviews.change` statuses, and the plan actions
// in resolution_policy. Anything else renders as `? VALUE` in the unknown tone.
const INTERPRETATION: Record<string, ToneSpec> = {
  PROCESSING: { icon: CircleDot, tone: 'progress' },
  EXTRACTED: { icon: CircleCheck, tone: 'good' },
  FAILED: { icon: CircleX, tone: 'bad' },
  INTERRUPTED: { icon: TriangleAlert, tone: 'warn', label: '提供方中断,结果不完整' },
}

const DISPOSITION: Record<string, ToneSpec> = {
  APPLIED: { icon: CircleCheck, tone: 'good' },
  PENDING_REVIEW: { icon: CircleDashed, tone: 'warn', label: '等待人工审批,未生效' },
  NO_ACTION: { icon: Circle, tone: 'neutral' },
  REJECTED: { icon: CircleX, tone: 'bad' },
  SOURCE_REPLAYED: { icon: RotateCcw, tone: 'neutral', label: '来源已被其他决议应用' },
}

const REVIEW: Record<string, ToneSpec> = {
  PENDING: { icon: CircleDashed, tone: 'warn', label: '待人工审批' },
  APPROVED: { icon: CircleCheck, tone: 'good' },
  REJECTED: { icon: CircleX, tone: 'bad' },
  SOURCE_REPLAYED: { icon: RotateCcw, tone: 'neutral' },
}

const ACTION: Record<string, ToneSpec> = {
  CREATE_RULE: { icon: Hash, tone: 'progress' },
  ATTACH_TASK: { icon: Rows3, tone: 'progress' },
  CREATE_TASK: { icon: Rows3, tone: 'progress' },
  NO_ACTION: { icon: Circle, tone: 'neutral' },
}

// P6-03c/P7-01. One vocabulary for the four status axes the Validation Inspector
// shows, because they share a token set: a recorded verdict, a record status, a
// manifest status and a file integrity. The literal token is always rendered, so a
// tone can never be mistaken for the value; an unrecognised value still renders as
// `? VALUE` in the unknown tone.
const VALIDATION: Record<string, ToneSpec> = {
  PASS: { icon: CircleCheck, tone: 'good' },
  FAIL: { icon: CircleX, tone: 'bad' },
  BLOCKED: { icon: OctagonX, tone: 'warn', label: '未运行,不等于通过' },
  ok: { icon: CircleCheck, tone: 'good' },
  corrupt: { icon: CircleX, tone: 'bad', label: '未通过校验:内容不可用' },
  duplicate: { icon: TriangleAlert, tone: 'bad', label: '同一 stem 有多份可解析记录:拒绝解析' },
  frozen: { icon: Snowflake, tone: 'good', label: 'manifest 存在且通过语法校验(未签名)' },
  absent: { icon: Circle, tone: 'neutral', label: '该 run 没有 bundle manifest' },
  match: { icon: CircleCheck, tone: 'good' },
  mismatch: { icon: CircleX, tone: 'bad', label: '字节与声明的 sha256/bytes 不一致' },
  missing: { icon: Circle, tone: 'neutral', label: '清单声明的文件不存在' },
  symlink_refused: { icon: OctagonX, tone: 'bad', label: '符号链接在打开时被拒绝' },
  unreadable: { icon: TriangleAlert, tone: 'bad', label: '文件存在但读不到' },
  attributed: { icon: SquareStack, tone: 'neutral' },
  unattributed: { icon: Circle, tone: 'warn', label: '没有项目归属:需要 manifest 的 projects' },
  // Evidence grades (`real > component > simulated > unknown`) and the record's own
  // `synthetic` marker. A synthetic record is graded `simulated` by the reader and can never
  // be reported as real, so its badge must not read as a success.
  real: { icon: Star, tone: 'good' },
  component: { icon: CircleDot, tone: 'progress', label: '组件级证据' },
  simulated: { icon: TriangleAlert, tone: 'warn', label: '模拟:不是真实证据' },
  unknown: { icon: CircleHelp, tone: 'unknown', label: '无法判定等级:不因缺少标记就当作 real' },
  synthetic: { icon: TriangleAlert, tone: 'warn', label: '记录自述 synthetic:不是证据' },
}

const VOCABULARY: Record<BadgeKind, Record<string, ToneSpec>> = {
  interpretation: INTERPRETATION,
  disposition: DISPOSITION,
  review: REVIEW,
  action: ACTION,
  validation: VALIDATION,
  step: STEP,
  task: TASK,
  evidence: EVIDENCE,
  reference: REFERENCE,
  rule: RULE,
  session: SESSION,
}

export function StatusBadge({
  kind,
  status,
  title,
}: {
  kind: BadgeKind
  status: string
  title?: string
}): JSX.Element {
  const spec = VOCABULARY[kind][status]
  const Icon = spec?.icon ?? CircleHelp
  const tone: Tone = spec?.tone ?? 'unknown'
  const hint = title ?? spec?.label
  return (
    <Badge
      variant={TONE_VARIANT[tone]}
      className={cls('badge', `badge--${tone}`)}
      data-status={status}
      title={hint ?? `${kind} status ${status}`}
    >
      {/* `badge__glyph` carries no style; the p6 browser trace finds the glyph by it. */}
      <Icon aria-hidden="true" className="badge__glyph" />
      <span className="badge__text">{status}</span>
    </Badge>
  )
}

// Tone -> mogi-ui badge variant. The variant supplies the mogi colours; the `badge--<tone>` class
// keeps the same tone for the app's own styling, so the two never disagree.
const TONE_VARIANT: Record<Tone, NonNullable<VariantProps<typeof badgeVariants>['variant']>> = {
  neutral: 'soft',
  progress: 'highlight',
  good: 'success',
  warn: 'warning',
  bad: 'destructive',
  unknown: 'destructive',
}

/** The set of Step statuses the board renders as columns, in workflow order. */
export const BOARD_COLUMNS: readonly string[] = [
  'PLANNED',
  'IN_PROGRESS',
  'EXECUTED',
  'VERIFIED',
  'ACCEPTED',
  'FAILED',
  'BLOCKED',
  'STALE',
  'SKIPPED',
]

/** Columns a Step may occupy that this build does not know about. */
export function unknownStepStatuses(statuses: readonly string[]): string[] {
  return statuses.filter((status) => !(status in STEP))
}
