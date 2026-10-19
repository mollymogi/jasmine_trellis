import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { MemoryHealth } from '../src/api/types'
import { MemoryHealthDashboard } from '../src/views/MemoryHealthDashboard'
afterEach(cleanup)
function health(overrides: Partial<MemoryHealth> = {}): MemoryHealth {
  return { status: 'unconfigured', configured: false, reachable: null, degraded: false, core_impact: 'none', engine_id: null, api_version: null, origin_host: null, features: {}, last_check_at: null, last_success_at: null, last_error_code: null, safe_error_summary: null, jobs: { pending: 0, running: 0, done: 1, failed: 0 }, items: { items_active: 1, items_obsolete: 0, items_deleted: 0, retain_failed: 0, delete_pending: 0, delete_failed: 0 }, deployment: {}, checked_at: '2026-10-19T08:00:00Z', non_authoritative: true, ...overrides }
}
describe('Memory health summary keeps Core reporting boundaries', () => {
  it('does not call an unconfigured engine healthy or erase the existing records', () => {
    render(<MemoryHealthDashboard health={health()} onRefresh={() => {}} details={<p>technical status fields</p>} />)
    expect(screen.getByText('尚未配置 Memory 引擎')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('已有记忆记录仍可查看')
    expect(screen.queryByText('Memory 最近运行正常')).toBeNull()
    expect(screen.queryByText('technical status fields')).toBeNull()
    expect(screen.getByText(/Core 主流程未受影响/)).toBeTruthy()
    expect(screen.getByRole('link', { name: /查看记忆条目/ }).getAttribute('href')).toBe('#/memory/items')
    fireEvent.click(screen.getByRole('button', { name: /查看运行详情/ }))
    expect(screen.getByText('technical status fields')).toBeTruthy()
  })
  it('does not turn a reachable idle engine into proven operation success', () => {
    render(<MemoryHealthDashboard health={health({ status: 'idle', configured: true, reachable: true })} onRefresh={() => {}} details={null} />)
    expect(screen.getByText('引擎已配置，尚无成功操作记录')).toBeTruthy()
    expect(screen.getByText('还不能据此确认记忆写入或召回已经可用。')).toBeTruthy()
    expect(screen.queryByText('Memory 最近运行正常')).toBeNull()
  })
  it('keeps the failing verdict even when the queue failed count is zero', () => {
    const h=health({ status: 'failing', core_impact: 'memory_paused' });h.items.retain_failed=1
    render(<MemoryHealthDashboard health={h} onRefresh={() => {}} details={null} />)
    expect(screen.getByText('有记忆操作处理失败')).toBeTruthy()
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText(/Memory 已暂停，Core 主流程仍可使用/)).toBeTruthy()
  })
  it('keeps unknown status and missing counts unknown instead of claiming success or zero', () => {
    const h=health({ status: 'FUTURE', core_impact: 'FUTURE', non_authoritative: false });h.jobs.pending=null as never;h.items.items_active=null as never
    render(<MemoryHealthDashboard health={h} onRefresh={() => {}} details={null} />)
    expect(screen.getByText('Memory 状态暂时无法确认')).toBeTruthy()
    expect(screen.getAllByText('未提供').length).toBeGreaterThan(1)
    expect(screen.getByText(/对 Core 的影响尚待核对/)).toBeTruthy()
    expect(screen.queryByText(/记忆仅供历史参考/)).toBeNull()
  })
})
