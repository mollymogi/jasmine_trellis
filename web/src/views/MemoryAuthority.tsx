/** ADR 0022: render Core's sampled relation; never infer conflicts from prose. */
import type { MemoryAuthorityComparison } from '../api/types'
import { SourceLink } from '../components/Trace'
import { RawText } from '../components/RawText'
import { formatTimestamp } from '../lib/text'
import { isCoreId } from '../lib/ids'
import { hrefFor } from '../app/router'

const LABELS: Record<string, string> = {
  VERSION_CHANGED: '历史决策已被替代',
  ORIGIN_RETIRED: '历史决策的来源已撤销',
  VERSION_MATCH: '关联版本仍有效（不代表语义一致或验证通过）',
  UNVERIFIABLE: '无法核对历史记忆与当前规则',
  UNAVAILABLE: '当前无法读取 Authority 对照',
  NOT_APPLICABLE: '此记忆不适用决策来源版本对照',
}

export function MemoryAuthority({ comparison }: { comparison?: MemoryAuthorityComparison }): JSX.Element {
  const known = comparison && Object.hasOwn(LABELS, comparison.status)
  const current = comparison?.current_rule
  const historical = comparison?.historical_rule
  const paired = comparison && ['VERSION_CHANGED', 'ORIGIN_RETIRED', 'VERSION_MATCH'].includes(comparison.status)
  const validPair = current && historical && isCoreId(current.rule_id, 'rul') &&
    Number.isInteger(current.version) && Number.isInteger(historical.version) &&
    current.version > 0 && historical.version > 0 && typeof current.content === 'string' &&
    typeof historical.content === 'string' && current.scope && typeof current.scope === 'object' &&
    Number.isInteger(current.revision) && current.revision > 0 &&
    isCoreId(current.origin_event_id, 'evt') && isCoreId(historical.origin_event_id, 'evt') && (
      comparison?.status === 'ORIGIN_RETIRED' ? current.status === 'RETIRED' :
      current.status === 'ACTIVE' && (comparison?.status === 'VERSION_MATCH'
        ? current.version === historical.version : current.version !== historical.version)
    )
  if (!known || (paired && !validPair)) return (
    <section data-testid="memory-authority" className="stack" aria-label="历史 Memory 与当前 Authority">
      <h3>历史 Memory 与当前 Authority</h3>
      <p role="status">对照不可用：Core 未提供可识别的完整结果，不能据此判断没有冲突。</p>
    </section>
  )
  const warning = comparison.status === 'VERSION_CHANGED' || comparison.status === 'ORIGIN_RETIRED'
  return (
    <section data-testid="memory-authority" className="stack" aria-label="历史 Memory 与当前 Authority">
      <h3>历史 Memory 与当前 Authority</h3>
      <p role={warning ? 'alert' : 'status'} data-testid="memory-authority-status">
        <strong>{LABELS[comparison.status]}</strong> · <code>{comparison.status}</code>
      </p>
      <p>依据：<code>{comparison.reason}</code>；Core 采样时间：{formatTimestamp(comparison.sampled_at)}。</p>
      <p>Memory 仅为历史参考，不能覆盖 Rule 或 Task State。ACTIVE / recallable 只表示生命周期状态，实际注入还需通过来源、当前规则和范围检查。</p>
      {comparison.status === 'UNAVAILABLE' ? <p>需要 authority:read；不会改用其他凭据。</p> : null}
      {comparison.status === 'UNVERIFIABLE' || comparison.status === 'NOT_APPLICABLE' ?
        <p>此对照只核对明确关联的规则版本，不按文字判断任意语义冲突。</p> : null}
      {paired && validPair ? <>
        <SourceLink kind="rule" id={current.rule_id} projectId={current.scope.project_id} taskId={current.scope.task_id} label="读取最新 Rule / 版本历史" />
        <p data-testid="memory-authority-versions">历史来源 v{historical.version} → 当前记录 v{current.version} · r{current.revision} · {current.status}</p>
        <RawText value={historical.content} label="历史来源版本内容" machine={false} />
        <SourceLink kind="event" id={historical.origin_event_id} label="历史来源 Event" />
        <RawText value={current.content} label="当前规则记录内容" machine={false} />
        <SourceLink kind="event" id={current.origin_event_id} label="当前来源 Event" />
        {current.status_event_id ? <SourceLink kind="event" id={current.status_event_id} label="当前状态变更 Event" /> : null}
        <p>以上是本次读取的快照。打开 Rule 链接或重新读取后，当前版本可能已变化。</p>
      </> : null}
    </section>
  )
}

export function HistoricalMemoryFilters({ filtered }: { filtered: unknown[] }): JSX.Element {
  const rows = filtered.filter((v): v is Record<string, unknown> => !!v && typeof v === 'object' &&
    'reason' in v && (v.reason === 'superseded_decision' || v.reason === 'retired_decision'))
  return <section data-testid="memory-authority-filters" className="stack">
    <p>以下是召回当时记录的过滤结果；当前规则需打开对应 Memory 重新核对。</p>
    {rows.length ? <p role="alert">{rows.length} 条历史决策因来源已被替代或撤销而未进入该次召回内容。</p> : null}
    {rows.map((row, index) => <p key={index}>
      <code>{String(row.reason)}</code>{' '}
      {typeof row.memory_id === 'string' && isCoreId(row.memory_id, 'mem') ?
        <a href={hrefFor({ name: 'memoryItem', memoryId: row.memory_id })}>查看历史 Memory 与当前 Authority</a> :
        '记录未提供可追溯的 Memory ID'}
    </p>)}
  </section>
}
