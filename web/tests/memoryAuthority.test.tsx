import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryAuthority, HistoricalMemoryFilters } from '../src/views/MemoryAuthority'
import type { MemoryAuthorityComparison } from '../src/api/types'
afterEach(cleanup)
const suffix = '01M4B6VGC29ESRDRQR62DX8AK8'
const event = `evt_${suffix}`
function comparison(status = 'VERSION_CHANGED'): MemoryAuthorityComparison {
  return {
    status, reason: 'superseded_decision', sampled_at: '2026-10-19T03:00:00Z',
    historical_rule: { version: 1, content: 'Use engine A <script>window.injected=1</script>', origin_event_id: event, change_event_id: event },
    current_rule: { rule_id: `rul_${suffix}`, version: 2, revision: 3, status: 'ACTIVE', content: 'Use engine B', scope: { kind: 'project', project_id: `prj_${suffix}`, task_id: null }, origin_event_id: event, change_event_id: event, status_event_id: event },
  }
}
describe('historical Memory / current Authority', () => {
  it('warns, separates versions, escapes HTML and links the actual sources', () => {
    const { container } = render(<MemoryAuthority comparison={comparison()} />)
    expect(screen.getByRole('alert').textContent).toContain('历史决策已被替代')
    expect(screen.getByTestId('memory-authority-versions').textContent).toContain('历史来源 v1 → 当前记录 v2')
    expect(container.textContent).toContain('Use engine A')
    expect(container.textContent).toContain('Use engine B')
    expect(container.querySelector('script')).toBeNull()
    expect(screen.getByText('当前来源 Event').closest('a')?.getAttribute('href')).toContain(event)
    expect(screen.getByText('读取最新 Rule / 版本历史').closest('a')?.getAttribute('href')).toContain(`rul_${suffix}`)
  })
  it('reports retirement without presenting old content as active', () => {
    const c = comparison('ORIGIN_RETIRED'); c.current_rule!.status = 'RETIRED'
    render(<MemoryAuthority comparison={c} />)
    expect(screen.getByRole('alert').textContent).toContain('来源已撤销')
  })
  it('a matching version is not semantic agreement or verification', () => {
    const c = comparison('VERSION_MATCH'); c.current_rule!.version = 1
    render(<MemoryAuthority comparison={c} />)
    expect(screen.getByTestId('memory-authority-status').textContent).toContain('不代表语义一致或验证通过')
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it.each(['UNAVAILABLE', 'UNVERIFIABLE', 'NOT_APPLICABLE'])('never invents a clear comparison for %s', (status) => {
    render(<MemoryAuthority comparison={{ ...comparison(status), current_rule: null, historical_rule: null }} />)
    expect(screen.queryByTestId('memory-authority-versions')).toBeNull()
    expect(screen.queryByText('没有冲突')).toBeNull()
    if (status === 'UNAVAILABLE') expect(screen.getByText(/需要 authority:read/)).toBeTruthy()
  })
  it('an older Core or unknown result is unavailable', () => {
    const view = render(<MemoryAuthority />)
    expect(screen.getByText(/Core 未提供可识别的完整结果/)).toBeTruthy()
    view.rerender(<MemoryAuthority comparison={comparison('FUTURE_STATUS')} />)
    expect(screen.getByText(/Core 未提供可识别的完整结果/)).toBeTruthy()
  })
  it('rejects an inconsistent success-shaped payload', () => {
    render(<MemoryAuthority comparison={comparison('VERSION_MATCH')} />)
    expect(screen.getByText(/不能据此判断没有冲突/)).toBeTruthy()
    expect(screen.queryByTestId('memory-authority-status')).toBeNull()
  })
  it('replaces an old sampled match after re-reading', () => {
    const c = comparison('VERSION_MATCH'); c.current_rule!.version = 1
    const view = render(<MemoryAuthority comparison={c} />)
    view.rerender(<MemoryAuthority comparison={comparison()} />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.queryByText(/关联版本仍有效/)).toBeNull()
  })
  it('trace reasons remain historical and withheld IDs get no invented link', () => {
    render(<HistoricalMemoryFilters filtered={[
      { reason: 'superseded_decision', memory_id: `mem_${suffix}` },
      { reason: 'retired_decision', memory_id: null },
      { reason: 'foreign_project', memory_id: null },
    ]} />)
    expect(screen.getByRole('alert').textContent).toContain('2 条历史决策')
    expect(screen.getByText(/召回当时记录的过滤结果/)).toBeTruthy()
    expect(screen.getAllByRole('link')).toHaveLength(1)
  })
})
