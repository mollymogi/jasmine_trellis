/**
 * Review detail and Resolution detail (P6-02 milestone A).
 *
 * `GET /v1/reviews/{rvw}` returns the review, its resolution, its change history
 * and (unless the plan can no longer be produced) a live `preview`; a failure to
 * produce that preview arrives as `preview_error` on an otherwise successful
 * response, which is rendered as a conflict panel rather than an empty plan.
 *
 * `GET /v1/resolutions/{res}` is the persisted decision: disposition, reasons,
 * actions, the plan Core stored, the expected context and any manual application
 * history. Approve/Reject are delivered on the review detail page; manual reapply is
 * not, and this file ships no control for it.
 */

import type { Resolution, Review } from '../api/types'
import { useEffect, useRef, useState } from 'react'

import { sourceHref } from '../app/router'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { JsonBlock } from '../components/JsonBlock'
import { WritePanel, parseJsonField } from '../components/WritePanel'
import { useWriteAction, type WriteAction } from '../app/useWriteAction'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, ErrorPanel, Loading, Panel, StaleBanner } from '../components/States'
import { KeyValue, SourceLink } from '../components/Trace'
import { formatTimestamp } from '../lib/text'
import { Badge } from '@/components/ui/badge'
import { TriangleAlert } from 'lucide-react'
import { HumanReview } from '../components/HumanReview'
import { Button } from '../components/ui/button'

export function ReviewView({ reviewId }: { reviewId: string }): JSX.Element {
  const { endpoints } = useSession()
  const review = useResource((signal) => endpoints.getReview(reviewId, signal), [reviewId])
  const item = review.data?.review ?? null
  // Both interfaces belong to one decision. Keep the hooks above the optional
  // advanced panel, and lock synchronously before either request can start.
  const approveWrite = useWriteAction((body, signal) => endpoints.approveReview(reviewId, body, signal), { targetKey: reviewId })
  const rejectWrite = useWriteAction((body, signal) => endpoints.rejectReview(reviewId, body, signal), { targetKey: reviewId })
  const locked = useRef(false)
  const blocked = approveWrite.busy || rejectWrite.busy ||
    [approveWrite.state, rejectWrite.state].some(state => state === 'unknown' || state === 'succeeded')
  useEffect(() => { locked.current = blocked }, [blocked, approveWrite.state, rejectWrite.state])
  const guard = (action: WriteAction): WriteAction => ({
    ...action,
    busy: blocked,
    submit: async body => {
      if (locked.current) return
      locked.current = true
      await action.submit(body)
    },
    resetDraft: () => { if (!locked.current) action.resetDraft() },
  })
  const approve = guard(approveWrite)
  const reject = guard(rejectWrite)

  const [advanced, setAdvanced] = useState(false)
  return <section className="review-detail-page" aria-label="审核建议">
    <nav className="breadcrumb" aria-label="位置"><a href={sourceHref.reviews(null)}>待审核</a><span aria-hidden="true">/</span><span>审核建议</span></nav>
    <div className="processing-heading"><div><h2>审核建议</h2><p>先核对原话，再决定是否采用。</p></div><Button size="sm" variant="outline" onClick={review.reload} disabled={review.status === 'loading'}>刷新</Button></div>
    {review.status === 'loading' && !item ? <Loading what="审核建议" /> : null}
    {review.error ? <ErrorPanel error={review.error} what="审核建议" onRetry={review.reload} /> : null}
    {item ? <><HumanReview key={item.review_id} item={item} approve={approve} reject={reject} onReadback={review.reload} />
      <Button size="sm" variant="ghost" onClick={() => setAdvanced(v => !v)}>{advanced ? '收起高级处理方案' : '高级处理方案与技术记录'}</Button>
      <div hidden={!advanced} className="reviews-advanced"><p className="muted">以下为协议级操作。必须逐项确认实际动作与载荷；不要把默认“不执行动作”当成采用建议。</p><ReviewBody item={item} /><ReviewWrites item={item} approve={approve} reject={reject} onReadback={review.reload} /></div>
    </> : null}
  </section>
}

function ReviewBody({ item }: { item: Review }): JSX.Element {
  return (
    <>
      <p className="evidencebadges">
        <strong>review</strong>
        <StatusBadge kind="review" status={item.status} />
        <span className="muted">revision {item.revision}</span>
      </p>
      <KeyValue
        rows={[
          { label: 'review_id', value: <code>{item.review_id}</code> },
          { label: 'status / revision', value: `${item.status} / ${item.revision}` },
          { label: 'created_at', value: formatTimestamp(item.created_at) },
          { label: 'updated_at', value: formatTimestamp(item.updated_at) },
          {
            label: 'resolution',
            value: (
              <a href={sourceHref.resolution(item.resolution_id)}>
                <code>{item.resolution_id}</code>
              </a>
            ),
          },
          {
            label: 'interpretation',
            value: (
              <a href={sourceHref.interpretation(item.resolution.interpretation_id)}>
                <code>{item.resolution.interpretation_id}</code>
              </a>
            ),
          },
          {
            label: '来源 Event',
            value: (
              <SourceLink
                kind="event"
                id={item.resolution.source_event_id}
                label="source Event"
              />
            ),
          },
          {
            label: 'disposition / reasons',
            value: (
              <>
                <StatusBadge kind="disposition" status={item.resolution.result.disposition} />{' '}
                {item.resolution.result.reasons.join(' · ') || '—'}
              </>
            ),
          },
        ]}
      />

      {item.preview ? (
        <>
          <h3>当前可审批计划(服务器预览)</h3>
          <p>
            <StatusBadge kind="disposition" status={item.preview.plan.disposition} /> ·{' '}
            {item.preview.plan.items.length} 项 · policy {item.preview.policy_version}
          </p>
          {item.preview.plan.items.length === 0 ? (
            <Empty what="计划项" hint="Core 返回了空计划;这不是「无需审批」的证明。" />
          ) : (
          <ul className="plain">
            {item.preview.plan.items.map((planItem) => (
              <li key={`${planItem.candidate_index}:${planItem.action}`}>
                <StatusBadge kind="action" status={planItem.action} />{' '}
                {planItem.pending ? <Badge variant="warning"><TriangleAlert aria-hidden="true" />待审批</Badge> : null}{' '}
                <code>{planItem.candidate.kind}</code> {planItem.candidate.content} ·{' '}
                {planItem.reason} · scope {planItem.candidate.scope.kind}
              </li>
            ))}
          </ul>
          )}
          <JsonBlock value={item.preview.expected_revisions} label="expected_revisions" />
        </>
      ) : item.preview_error ? (
        <div className="state state--pending" role="alert">
          <p className="state__headline">
            预览冲突 — <code>{item.preview_error.code}</code>
          </p>
          <p>
            该 Review 仍然存在,但 Core 现在无法给出可审批的计划(例如解释已不是当前版本)。这不是空计划。
          </p>
          <JsonBlock value={item.preview_error.details} label="preview_error.details" startsOpen />
        </div>
      ) : (
        <Empty what="预览" hint="Core 既未返回 preview,也未返回 preview_error。" />
      )}

      <h3>变更历史({item.history.length})</h3>
      {item.history.length === 0 ? (
        <p className="muted">尚无变更(PENDING 状态没有人工动作记录)。</p>
      ) : (
        <ul className="plain">
          {item.history.map((change, index) => (
            <li key={index}>
              <JsonBlock value={change} label={`history[${index}]`} />
            </li>
          ))}
        </ul>
      )}
      <JsonBlock value={item.resolution} label="resolution(完整记录)" />
    </>
  )
}

/**
 * Approve / Reject. Approve needs the *server's* preview (`expected_revisions`,
 * `expected_context_digest`) and one action per candidate; when Core cannot
 * produce a preview the panel refuses to open approve rather than inventing a
 * plan. The draft survives a `revision_conflict`.
 */
function ReviewWrites({ item, onReadback, approve, reject }: { item: Review; onReadback: () => void; approve: WriteAction; reject: WriteAction }): JSX.Element {
  const suggested = (item.preview?.plan.items ?? []).map((planItem) => ({
    candidate_index: planItem.candidate_index,
    action: planItem.action,
    payload: {},
  }))
  const [draft, setDraft] = useState(() => JSON.stringify(suggested, null, 2))
  useEffect(() => {
    setDraft(JSON.stringify(suggested, null, 2))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.review_id, item.revision])
  const parsed = parseJsonField(draft, 'actions')

  const candidateCount = item.preview?.plan.items.length ?? 0
  const actionsValid =
    parsed.error === undefined &&
    Array.isArray(parsed.value) &&
    parsed.value.length === candidateCount &&
    parsed.value.every(
      (entry) =>
        typeof entry === 'object' &&
        entry !== null &&
        typeof (entry as { candidate_index?: unknown }).candidate_index === 'number' &&
        typeof (entry as { action?: unknown }).action === 'string' &&
        typeof (entry as { payload?: unknown }).payload === 'object' &&
        (entry as { payload?: unknown }).payload !== null,
    )
  const actionsError = parsed.error ?? (actionsValid ? undefined : 'actions 必须为每个候选各一条 {candidate_index, action, payload}')

  const basis = `review ${item.review_id} · status ${item.status} · revision ${item.revision}`

  return (
    <>
      {item.preview ? (
        <WritePanel
          title="Approve(按服务器计划批准)"
          basis={basis}
          action={approve}
          targetKey={item.review_id}
          submitLabel="提交批准"
          ready={item.status === 'PENDING' && actionsValid && !approve.busy}
          onSucceeded={onReadback}
          fields={
            <>
              <p className="muted">
                计划来自 Core 的 preview(policy {item.preview.policy_version}),不是前端推断;
                <code>expected_revisions</code> 与 <code>expected_context_digest</code> 原样回传。
              </p>
              <JsonBlock value={item.preview.expected_revisions} label="expected_revisions(原样回传)" />
              <label className="write-field">
                actions(每个候选一条,可改为 NO_ACTION 等 Core 允许的动作)
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  rows={8}
                  spellCheck={false}
                />
                {actionsError ? <span className="state state--error">{actionsError}</span> : null}
                <span className="muted">
                  批准的实际 write 由 Core 执行;若来源已被其它决议应用,Core 会返回
                  <code> SOURCE_REPLAYED</code> 而不是重复写。
                </span>
              </label>
            </>
          }
          build={({ reason, hostId }) =>
            actionsValid
              ? {
                  reason,
                  host_id: hostId,
                  expected_revision: item.revision,
                  expected_revisions: item.preview?.expected_revisions ?? [],
                  expected_context_digest: item.preview?.expected_context_digest ?? '',
                  actions: parsed.value,
                }
              : 'actions 不合法'
          }
        />
      ) : (
        <div className="state state--pending" data-testid="approve-unavailable">
          <p className="state__headline">Approve 不可用:Core 当前给不出可审批的计划</p>
          <p>
            该 Review 仍是 PENDING,但 preview 失败(见上),因此没有
            <code> expected_revisions</code> 与 <code>expected_context_digest</code> 可以回传。
            本页不会自行拼一个计划;请先处理上面的冲突,或只使用 Reject。
          </p>
        </div>
      )}

      <WritePanel
        title="Reject(拒绝该计划)"
        basis={basis}
        action={reject}
        targetKey={item.review_id}
        submitLabel="提交拒绝"
        ready={item.status === 'PENDING' && !reject.busy}
        onSucceeded={onReadback}
        fields={<p className="muted">拒绝不会改动 Truth,也不会删除原解释与来源 Event。</p>}
        build={({ reason, hostId }) => ({ reason, host_id: hostId, expected_revision: item.revision })}
      />
    </>
  )
}

export function ResolutionView({ resolutionId }: { resolutionId: string }): JSX.Element {
  const { endpoints } = useSession()
  const resolution = useResource(
    (signal) => endpoints.getResolution(resolutionId, signal),
    [resolutionId],
  )
  const item = resolution.data?.resolution ?? null

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="位置">
        <span>Resolution</span>
        <span aria-hidden="true">/</span>
        <code>{resolutionId}</code>
      </nav>

      <Panel
        title="Resolution(已持久化的决议)"
        actions={
          <button type="button" onClick={resolution.reload} disabled={resolution.status === 'loading'}>
            重新读取
          </button>
        }
      >
        {resolution.status === 'loading' && item === null ? <Loading what="Resolution" /> : null}
        {resolution.error ? (
          <ErrorPanel error={resolution.error} what="Resolution" onRetry={resolution.reload} />
        ) : null}
        {item ? <ResolutionBody item={item} /> : null}
        {resolution.loadedAt ? (
          <StaleBanner loadedAt={resolution.loadedAt} scope={`Resolution ${resolutionId}`} />
        ) : null}
      </Panel>
    </div>
  )
}

function ResolutionBody({ item }: { item: Resolution }): JSX.Element {
  const result = item.result
  return (
    <>
      <p className="evidencebadges">
        <strong>disposition</strong>
        <StatusBadge kind="disposition" status={result.disposition} />
        <span className="muted">policy {item.policy_version}</span>
      </p>
      <KeyValue
        rows={[
          { label: 'resolution_id', value: <code>{item.resolution_id}</code> },
          {
            label: 'interpretation',
            value: (
              <a href={sourceHref.interpretation(item.interpretation_id)}>
                <code>{item.interpretation_id}</code>
              </a>
            ),
          },
          {
            label: '来源 Event',
            value: <SourceLink kind="event" id={item.source_event_id} label="source Event" />,
          },
          {
            label: 'command Event',
            value: <SourceLink kind="event" id={item.command_event_id} label="command Event" />,
          },
          { label: 'actor', value: <code>{item.actor_id}</code> },
          { label: 'created_at', value: formatTimestamp(item.created_at) },
          { label: 'reasons', value: result.reasons.join(' · ') || '—' },
          {
            label: 'execution actor',
            value: result.execution_actor_id ?? '—(未由系统执行)',
          },
          {
            label: 'canonical / latest application',
            value: (
              <>
                {result.canonical_resolution_id ? (
                  <a href={sourceHref.resolution(result.canonical_resolution_id)}>
                    <code>{result.canonical_resolution_id}</code>
                  </a>
                ) : '—'}
                {' · '}
                {result.latest_application_id ?? '—'}
              </>
            ),
          },
          {
            label: 'review',
            value: result.review_id ? (
              <a href={sourceHref.review(result.review_id)}><code>{result.review_id}</code></a>
            ) : '—(无需人工审批)',
          },
          {
            label: 'review actor / command',
            value: result.review_actor_id ? (
              <>
                <code>{result.review_actor_id}</code>
                {result.review_command_event_id ? (
                  <> · <SourceLink kind="event" id={result.review_command_event_id} label="review command" /></>
                ) : null}
              </>
            ) : '—',
          },
          { label: 'context_digest', value: <code className="wrap">{item.context_digest}</code> },
          { label: 'request_hash', value: <code className="wrap">{item.request_hash}</code> },
        ]}
      />

      <h3>Actions({result.actions.length})</h3>
      {result.actions.length === 0 ? (
        <Empty
          what="action"
          hint="该决议没有产生动作(例如 PENDING_REVIEW 尚未执行、或被拒绝)。"
        />
      ) : (
        <JsonBlock value={result.actions} label="result.actions" startsOpen />
      )}

      <JsonBlock value={item.plan} label="plan(持久化原文)" />
      <JsonBlock value={item.expected_context} label="expected_context" />
      <JsonBlock value={result.source_bindings} label="source_bindings" />
      {item.manual_application_history.length > 0 ? (
        <JsonBlock value={item.manual_application_history} label="manual_application_history" />
      ) : (
        <p className="muted">没有人工重新应用历史。</p>
      )}
    </>
  )
}
