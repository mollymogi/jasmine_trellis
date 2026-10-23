import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import type { RawEvent, Review } from '../src/api/types'
import { conventionActions, reviewAgent } from '../src/lib/reviewPresentation'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

const event: RawEvent = { seq: 1, schema_version: 1, source_event_id: null, recorded_at: '2026-10-23T08:00:00Z', actor_id: 'act_human', body_sha256: 'a'.repeat(64), event_id: 'evt_source', actor_kind: 'human', event_type: 'user.prompt', source_system: 'claude-native-v1', project_id: 'prj_source', task_id: null, session_id: 'ses_source', host_id: 'hst_1', occurred_at: '2026-10-23T08:00:00Z', payload: { text: '项目约定：文档默认使用中文。' } }
function review(): Review {
  const plan = { policy_version: 'v1', disposition: 'PENDING_REVIEW', items: [{ candidate_index: 0, action: 'NO_ACTION', pending: true, reason: 'scope_or_impact_requires_review', candidate: { kind: 'RULE', content: '文档默认使用中文。', impact: 'LOW', certainty: 'EXPLICIT', confidence: 1, rationale: '用户明确要求', scope: { kind: 'PROJECT', project_id: 'prj_source', task_id: null, path: null, tool: null }, source_span: { start: 0, end: 15, quote: '项目约定：文档默认使用中文。' } } }] }
  return { review_id: 'rvw_1', resolution_id: 'res_1', status: 'PENDING', revision: 2, created_at: '2026-10-23T08:00:00Z', updated_at: '2026-10-23T08:00:00Z', history: [], resolution: { resolution_id: 'res_1', source_event_id: 'evt_source', interpretation_id: 'int_1', plan, result: { reasons: ['scope_or_impact_requires_review'] } } as Review['resolution'], preview: { plan, expected_revisions: [{ object_type: 'project', object_id: 'prj_source', revision: 5 }], expected_context_digest: 'server-digest', policy_version: 'v1' } as Review['preview'] }
}
let fake: FakeCore | null = null
afterEach(() => { fake?.restore(); fake = null; location.hash = '' })
async function enter(hash: string, row = review(), routes: Parameters<typeof installFakeCore>[0] = {}, handler?: Parameters<typeof installFakeCore>[1]) {
  fake = installFakeCore({ 'GET /v1/health': { body: { status: 'ok', core_version: '0.2.0', schema_version: 14 } }, 'GET /v1/projects': { body: { projects: [{ project_id: 'prj_source', name: '真实项目' }], count: 1 } }, 'GET /v1/projects/prj_source': { body: { project: { name: '真实项目' } } }, 'GET /v1/events/evt_source': { body: { event } }, 'GET /v1/hosts': { body: { hosts: [{ host_id: 'hst_1', display_name: '本机' }] } }, 'GET /v1/reviews/pending': { body: { items: [row], next_after_id: null } }, 'GET /v1/reviews/rvw_1': { body: { review: row } }, ...routes }, handler)
  const user = userEvent.setup()
  render(<SessionProvider><App /></SessionProvider>)
  await user.type(screen.getByLabelText('Core API token'), 'test-token')
  await user.click(screen.getByRole('button', { name: '使用此 token' }))
  await screen.findByText('Core 健康与 Schema')
  location.hash = hash; window.dispatchEvent(new HashChangeEvent('hashchange'))
  return user
}

describe('human review presentation', () => {
  it('shows content, real source agent, project and Chinese reasons, keeping IDs and protocol fields off the default list', async () => {
    await enter('#/reviews')
    const table = await screen.findByRole('table', { name: '待审核建议列表' })
    await within(table).findByText('Claude')
    expect(table.textContent).toContain('文档默认使用中文。')
    expect(table.textContent).toContain('真实项目')
    expect(table.textContent).toContain('涉及适用范围或较大影响')
    expect(table.textContent).toContain('待审核')
    expect(table.textContent).not.toMatch(/rvw_|res_|int_|evt_|PENDING|revision/)
    expect(document.querySelector('.readonly-banner')).toBeNull()
    expect(fake!.requests.filter(r => r.method === 'POST')).toHaveLength(0)
  })
  it('keeps a failed source visibly unknown instead of using the extractor identity', async () => {
    await enter('#/reviews', review(), { 'GET /v1/events/evt_source': coreError(403, 'forbidden', 'missing scope') })
    await screen.findByText('来源待确认')
    expect(screen.queryByText('Codex')).toBeNull()
    expect(screen.getByRole('table').textContent).toContain('文档默认使用中文。')
  })
  it('keeps loaded items on next-page failure and uses only the server cursor', async () => {
    const row = review()
    const user = await enter('#/reviews', row, {}, r => r.path === '/v1/reviews/pending' ? r.query.get('after_id') ? coreError(503, 'database_busy', 'busy') : { body: { items: [row], next_after_id: 'server-cursor' } } : undefined)
    await user.click(await screen.findByRole('button', { name: '载入更多建议' }))
    await screen.findByRole('alert')
    expect(screen.getByRole('table').textContent).toContain('文档默认使用中文。')
    expect(fake!.callsTo('GET', '/v1/reviews/pending').at(-1)?.query.get('after_id')).toBe('server-cursor')
  })
  it('requires a human decision and explanation, then submits narrow CONTEXT actions with server revisions', async () => {
    const user = await enter('#/review/rvw_1', review(), { 'POST /v1/reviews/rvw_1/approve': { status: 201, body: { replayed: false } } })
    await user.click(await screen.findByRole('button', { name: '批准为项目约定' }))
    const confirm = screen.getByRole('button', { name: '确认批准' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(fake!.requests.filter(r => r.method === 'POST')).toHaveLength(0)
    await user.type(screen.getByLabelText('处理说明'), '确认使用中文')
    await user.click(confirm)
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/reviews/rvw_1/approve')).toHaveLength(1))
    const body = fake!.callsTo('POST', '/v1/reviews/rvw_1/approve')[0]!.body!
    expect(body).toMatchObject({ expected_revision: 2, expected_context_digest: 'server-digest', expected_revisions: [{ object_type: 'project', object_id: 'prj_source', revision: 5 }], host_id: 'hst_1', reason: '确认使用中文', actions: [{ candidate_index: 0, action: 'CREATE_RULE', payload: { kind: 'RULE', content: '文档默认使用中文。', enforcement: 'CONTEXT', severity: 'NORMAL', matcher: {} } }] })
    expect(typeof body.idempotency_key).toBe('string')
    const link = screen.getByRole('link', { name: '回到来源对话' })
    expect(link.getAttribute('href')).toContain('session_id=ses_source')
    expect(link.getAttribute('href')).not.toContain('task_id=ses_source')
  })
  it('does not manufacture an approval plan for inferred tasks; rejection keeps the audit contract', async () => {
    const row = review(); row.preview!.plan.items[0]!.candidate.certainty = 'INFERRED'
    row.preview!.plan.items[0]!.candidate.kind = 'TASK_CREATE_OR_ATTACH'
    const user = await enter('#/review/rvw_1', row, { 'POST /v1/reviews/rvw_1/reject': { status: 201, body: { replayed: false } } })
    await user.click(await screen.findByRole('button', { name: '不采纳这条建议' }))
    expect(screen.queryByRole('button', { name: '批准为项目约定' })).toBeNull()
    await user.type(screen.getByLabelText('处理说明'), '只是临时请求，无需保存')
    await user.click(screen.getByRole('button', { name: '确认不采纳' }))
    await waitFor(() => expect(fake!.callsTo('POST', '/v1/reviews/rvw_1/reject')).toHaveLength(1))
    expect(Object.keys(fake!.callsTo('POST', '/v1/reviews/rvw_1/reject')[0]!.body!).sort()).toEqual(['expected_revision', 'host_id', 'idempotency_key', 'reason'])
  })
  it('blocks another decision after an unknown result, without silently retrying', async () => {
    const user = await enter('#/review/rvw_1', review(), { 'POST /v1/reviews/rvw_1/reject': { networkError: 'connection closed' } })
    await user.click(await screen.findByRole('button', { name: '不采纳这条建议' }))
    await user.type(screen.getByLabelText('处理说明'), '先核对提交结果')
    await user.click(screen.getByRole('button', { name: '确认不采纳' }))
    await screen.findByText(/提交结果尚未确认/)
    expect((screen.getByRole('button', { name: '确认不采纳' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '批准为项目约定' }) as HTMLButtonElement).disabled).toBe(true)
    expect(fake!.callsTo('POST', '/v1/reviews/rvw_1/reject')).toHaveLength(1)
    expect(fake!.callsTo('POST', '/v1/reviews/rvw_1/approve')).toHaveLength(0)
  })
  it('keeps credentials masked in list summaries and searches', async () => {
    const row = review(); row.preview!.plan.items[0]!.candidate.content = '不要输出 sk-proj-1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN'
    const user = await enter('#/reviews', row)
    const table = await screen.findByRole('table')
    expect(table.textContent).not.toContain('sk-proj-1234567890')
    await user.type(screen.getByRole('textbox', { name: '搜索已载入建议' }), 'sk-proj-1234567890')
    await screen.findByText('已载入的建议中没有匹配内容')
  })
  it('preserves a conflict and the explanation instead of retrying or declaring success', async () => {
    const user = await enter('#/review/rvw_1', review(), { 'POST /v1/reviews/rvw_1/reject': coreError(409, 'revision_conflict', 'changed') })
    await user.click(await screen.findByRole('button', { name: '不采纳这条建议' }))
    await user.type(screen.getByLabelText('处理说明'), '保留此处理说明')
    await user.click(screen.getByRole('button', { name: '确认不采纳' }))
    await screen.findByText(/审核依据已变化/)
    expect((screen.getByLabelText('处理说明') as HTMLTextAreaElement).value).toBe('保留此处理说明')
    expect(fake!.callsTo('POST', '/v1/reviews/rvw_1/reject')).toHaveLength(1)
  })
})

it('never broadens task, mixed, cross-project or nonhuman sources into project conventions', () => {
  for (const field of ['kind', 'certainty', 'scope'] as const) {
    const row = review(), c = row.preview!.plan.items[0]!.candidate
    if (field === 'kind') c.kind = 'CORRECTION'
    if (field === 'certainty') c.certainty = 'INFERRED'
    if (field === 'scope') c.scope.project_id = 'another-project'
    expect(conventionActions(row, event)).toBeNull()
  }
  expect(conventionActions(review(), { ...event, actor_kind: 'agent' })).toBeNull()
  expect(conventionActions(review(), undefined)).toBeNull()
  const row = review(); row.preview = undefined
  expect(conventionActions(row, event)).toBeNull()
  expect(reviewAgent({ ...event, source_system: 'custom-internal' }).name).toBe('其他来源')
})
