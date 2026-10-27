import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it } from 'vitest'
import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { coreError, installFakeCore, type FakeCore } from './helpers/fakeCore'

const suffix = '01M4B6VGC29ESRDRQR62DX8AK8', memoryId = `mem_${suffix}`, projectId = `prj_${suffix}`
function item(overrides: Record<string, unknown> = {}) {
  return { memory_id: memoryId, project_id: projectId, task_id: null, scope_kind: 'project', memory_type: 'STABLE_PROJECT_FACT', sanitized_text: '项目文档统一使用中文。', status: 'ACTIVE', engine_state: 'RETAINED', pinned: false, revision: 1, source_event_id: `evt_${suffix}`, origin_rule_id: null, origin_rule_version: null, origin_event_id: null, evidence_ids: [], receipt: null, recall_count: 0, created_at: '2026-10-23T08:00:00Z', updated_at: '2026-10-23T08:00:00Z', lifecycle: { status: 'ACTIVE', recallable: true, pinned: false, engine_state: 'RETAINED' }, ...overrides }
}
let fake: FakeCore | null = null
const observedTypes = [
  ['OBSERVED_CONVENTION', '对话中的约定'],
  ['OBSERVED_DECISION', '对话中的决定'],
  ['OBSERVED_CORRECTION', '对话中的纠正'],
  ['OBSERVED_ACCEPTANCE', '对话中的验收'],
]
afterEach(() => { fake?.restore(); fake = null; location.hash = '' })
async function enter(routes: Parameters<typeof installFakeCore>[0] = {}, handler?: Parameters<typeof installFakeCore>[1], hash = '#/memory/items') {
  fake = installFakeCore({ 'GET /v1/health': { body: { status: 'ok', core_version: '0.2.0', schema_version: 14 } }, 'GET /v1/projects': { body: { projects: [{ project_id: projectId, name: '文档项目' }], count: 1 } }, ['GET /v1/projects/' + projectId]: { body: { project: { name: '文档项目' } } }, 'GET /v1/memory/items': { body: { items: [item()], count: 1, non_authoritative: true } }, ...routes }, handler)
  const user = userEvent.setup()
  render(<SessionProvider><App /></SessionProvider>)
  await user.type(screen.getByLabelText('Core API token'), 'test-token')
  await user.click(screen.getByRole('button', { name: '使用此 token' }))
  await screen.findByText('Core 健康与 Schema')
  location.hash = hash; window.dispatchEvent(new HashChangeEvent('hashchange'))
  return user
}

it('shows saved content and Chinese labels, without putting IDs or engine fields in the default table', async () => {
  await enter()
  const table = await screen.findByRole('table', { name: '记忆内容列表' })
  await within(table).findByText('文档项目')
  expect(table.textContent).toContain('项目文档统一使用中文。')
  expect(table.textContent).toContain('项目约定与事实')
  expect(table.textContent).toContain('已保存至引擎')
  expect(table.textContent).not.toMatch(/mem_|evt_|prj_|ACTIVE|RETAINED|revision/)
  expect(screen.getByText('已载入 1 条')).toBeTruthy()
  expect(document.querySelector('.readonly-banner')).toBeNull()
  expect(fake!.callsTo('GET', '/v1/conversation-processing')).toHaveLength(0)
  expect(fake!.requests.filter(r => r.method === 'POST')).toHaveLength(0)
})

it.each(observedTypes)('displays and filters %s in the catalog', async (type, label) => {
  const user = await enter({ 'GET /v1/memory/items': { body: { items: [item({ memory_type: type })], count: 1, non_authoritative: true } } })
  const table = await screen.findByRole('table', { name: '记忆内容列表' })
  expect(within(table).getByText(label)).toBeTruthy()
  await user.selectOptions(screen.getByLabelText('记忆类型'), type)
  await waitFor(() => expect(fake!.callsTo('GET', '/v1/memory/items').at(-1)!.query.get('memory_type')).toBe(type))
})

it.each(observedTypes)('displays %s on memory detail', async (type, label) => {
  await enter({ ['GET /v1/memory/items/' + memoryId]: { body: { memory: item({ memory_type: type }), recallable: true, non_authoritative: true, sources: [], jobs: [], lifecycle: {}, engine_claim: 'fixture', authority_comparison: { status: 'NOT_APPLICABLE', reason: 'not_decision_memory', sampled_at: '2026-10-23T08:00:00Z', current_rule: null, historical_rule: null } } } }, undefined, '#/memory/items/' + memoryId)
  await screen.findByText('项目文档统一使用中文。')
  expect(screen.getAllByText(label)).toHaveLength(2)
  expect(screen.queryByText('其他记忆类型')).toBeNull()
})

it('reports processing and writing as separate stages, and never calls applied tasks saved memories', async () => {
  const user = await enter({ 'GET /v1/conversation-processing': { body: { count: 146, counts: { APPLIED: 29, PENDING_REVIEW: 28, FAILED: 42, PROCESSING: 1, NO_ACTION: 46 } } }, 'GET /v1/memory/health': { body: { jobs: { pending: 0, running: 0, failed: 0, done: 8 } } } })
  await user.click(await screen.findByText('为什么对话很多，记忆却很少？'))
  const stats = await screen.findByText('已应用')
  expect(stats.parentElement?.textContent).toContain('29')
  expect(screen.getByText('提炼失败').parentElement?.textContent).toContain('42')
  expect(screen.getByText(/已应用包含创建、关联任务/)).toBeTruthy()
  expect(screen.getByText(/已完成任务 8 不等于当前保留的记忆条数/)).toBeTruthy()
  expect(screen.queryByText('已保存 29 条记忆')).toBeNull()
  const request = fake!.callsTo('GET', '/v1/conversation-processing')[0]!
  expect(request.query.get('limit')).toBe('1')
  expect(fake!.requests.filter(r => r.method === 'POST')).toHaveLength(0)
})

it('keeps saved memory visible when the optional explanation lacks permissions, without inventing zero failures', async () => {
  const user = await enter({ 'GET /v1/conversation-processing': coreError(403, 'forbidden_scope', 'scope'), 'GET /v1/memory/health': coreError(403, 'forbidden_scope', 'scope') })
  await user.click(await screen.findByText('为什么对话很多，记忆却很少？'))
  await screen.findByText(/提炼进度暂时不可读取/)
  await screen.findByText(/不能据此认定没有失败/)
  expect(screen.getByRole('table').textContent).toContain('项目文档统一使用中文。')
  expect(document.querySelector('.memory-intake-counts')).toBeNull()
})

it('passes project, type and lifecycle filters to Core while content search uses only masked loaded text', async () => {
  const user = await enter({ 'GET /v1/memory/items': { body: { items: [item({ sanitized_text: '不要输出 sk-proj-1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN' })], count: 1, non_authoritative: true } } })
  const table = await screen.findByRole('table')
  expect(table.textContent).not.toContain('sk-proj-1234567890')
  await user.selectOptions(screen.getByLabelText('记忆所属项目'), projectId)
  await waitFor(() => expect(fake!.callsTo('GET', '/v1/memory/items').at(-1)!.query.get('project_id')).toBe(projectId))
  await user.selectOptions(screen.getByLabelText('记忆类型'), 'STABLE_PROJECT_FACT')
  await user.selectOptions(screen.getByLabelText('记忆状态'), 'OBSOLETE')
  await waitFor(() => expect(fake!.callsTo('GET', '/v1/memory/items').at(-1)!.query.get('status')).toBe('OBSOLETE'))
  expect(fake!.callsTo('GET', '/v1/memory/items').at(-1)!.query.get('memory_type')).toBe('STABLE_PROJECT_FACT')
  await user.type(screen.getByRole('textbox', { name: '搜索已载入记忆' }), 'sk-proj-1234567890')
  await screen.findByText('已载入的记忆中没有匹配内容')
})

it('searches the full masked memory text beyond the row preview', async () => {
  const keyword = 'TAILKEYWORD'
  const user = await enter({ 'GET /v1/memory/items': { body: { items: [item({ sanitized_text: '前'.repeat(180) + keyword })], count: 1, non_authoritative: true } } })
  const table = await screen.findByRole('table', { name: '记忆内容列表' })
  expect(table.textContent).not.toContain(keyword)
  await screen.findByText('当前查询范围已读取完毕')
  await user.type(screen.getByRole('textbox', { name: '搜索已载入记忆' }), keyword.toLowerCase())
  expect(screen.getByRole('table', { name: '记忆内容列表' }).querySelectorAll('tbody tr')).toHaveLength(1)
  expect(screen.getByText(/匹配 1 条/)).toBeTruthy()
  expect(screen.queryByText('已载入的记忆中没有匹配内容')).toBeNull()
})

it('pages by the actual last memory ID, retains a failed page and drops the authority claim if any page omits it', async () => {
  let failed = true
  const first = Array.from({ length: 50 }, (_, i) => item({ memory_id: `mem_first_${i}` }))
  const user = await enter({}, r => {
    if (r.path !== '/v1/memory/items') return undefined
    if (!r.query.get('after_memory_id')) return { body: { items: first, count: 50, non_authoritative: true } }
    return failed ? coreError(503, 'database_busy', 'busy') : { body: { items: [item({ memory_id: 'mem_last' })], count: 1 } }
  })
  await user.click(await screen.findByRole('button', { name: '载入更多记忆' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('table').querySelectorAll('tbody tr')).toHaveLength(50)
  expect(fake!.callsTo('GET', '/v1/memory/items').at(-1)!.query.get('after_memory_id')).toBe('mem_first_49')
  failed = false
  await user.click(screen.getByRole('button', { name: '载入更多记忆' }))
  await waitFor(() => expect(screen.getByRole('table').querySelectorAll('tbody tr')).toHaveLength(51))
  expect(screen.queryByTestId('non-authoritative')).toBeNull()
  expect(screen.getByText('当前查询范围已读取完毕')).toBeTruthy()
})

it('discards a delayed old-project response after switching scope', async () => {
  const other = 'prj_01M4B6VGC29ESRDRQR62DX8AK9'
  const user = await enter({ 'GET /v1/projects': { body: { projects: [{ project_id: projectId, name: '项目 A' }, { project_id: other, name: '项目 B' }], count: 2 } } }, r => r.path !== '/v1/memory/items' ? undefined : r.query.get('project_id') === projectId ? { delayMs: 80, body: { items: [item({ sanitized_text: '来自 A 的延迟内容' })], count: 1 } } : r.query.get('project_id') === other ? { body: { items: [item({ project_id: other, sanitized_text: '来自 B 的当前内容' })], count: 1 } } : undefined)
  await screen.findByRole('table')
  await user.selectOptions(screen.getByLabelText('记忆所属项目'), projectId)
  await user.selectOptions(screen.getByLabelText('记忆所属项目'), other)
  await screen.findByText('来自 B 的当前内容')
  await new Promise(resolve => setTimeout(resolve, 100))
  expect(screen.queryByText('来自 A 的延迟内容')).toBeNull()
})

it('does not mistake ACTIVE lifecycle for engine retention', async () => {
  await enter({ 'GET /v1/memory/items': { body: { items: [item({ engine_state: 'PENDING_RETAIN' })], count: 1, non_authoritative: true } } })
  const table = await screen.findByRole('table')
  expect(table.textContent).toContain('保留中')
  expect(table.textContent).toContain('等待写入')
  expect(table.textContent).not.toContain('已保存至引擎')
})

it('exposes the full memory text directly on detail while keeping current-authority checks and source controls', async () => {
  await enter({ ['GET /v1/memory/items/' + memoryId]: { body: { memory: item({ memory_type: 'VERIFIED_OUTCOME' }), recallable: true, non_authoritative: true, sources: [], jobs: [], lifecycle: {}, engine_claim: 'fixture', authority_comparison: { status: 'NOT_APPLICABLE', reason: 'not_decision_memory', sampled_at: '2026-10-23T08:00:00Z', current_rule: null, historical_rule: null } } } }, undefined, '#/memory/items/' + memoryId)
  const body = await screen.findByText('项目文档统一使用中文。')
  expect(body.closest('[role="dialog"]')).toBeNull()
  expect(screen.getByRole('button', { name: /查看条目与来源/ })).toBeTruthy()
  expect(screen.getByRole('button', { name: /查看判定依据/ })).toBeTruthy()
  expect(screen.getAllByText('有验证依据的结果')).toHaveLength(2)
  expect(fake!.requests.filter(r => r.method === 'POST')).toHaveLength(0)
})
