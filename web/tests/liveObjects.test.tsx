import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useLiveObjects } from '../src/app/useLiveObjects'

afterEach(() => { vi.useRealTimers() })
const flush = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }

it('drains object pages, discovers new rows, and recovers without losing rows', async () => {
  vi.useFakeTimers()
  let rows = Array.from({ length: 101 }, (_, i) => i)
  let fail = false
  const fetch = vi.fn(async (offset: number, limit: number) => {
    if (fail) throw new Error('offline')
    const items = rows.slice(offset, offset + limit)
    return { items, count: items.length }
  })
  const { result, unmount } = renderHook(() => useLiveObjects(fetch, 'A'))
  await flush()
  expect(result.current.items).toHaveLength(101)
  fail = true
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(result.current.items).toHaveLength(101)
  expect(result.current.error).not.toBeNull()
  fail = false; rows = [...rows, 101]
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(result.current.items).toHaveLength(102)
  expect(result.current.error).toBeNull()
  expect(fetch.mock.calls.at(-1)?.[0]).toBe(101)
  unmount()
  const calls = fetch.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(fetch).toHaveBeenCalledTimes(calls)
})

it('cannot mix a delayed old project response into the new project', async () => {
  vi.useFakeTimers()
  let oldResolve: (value: { items: string[]; count: number }) => void = () => {}
  const { result, rerender } = renderHook(({ scope }) => useLiveObjects<string>(
    () => scope === 'A' ? new Promise(resolve => { oldResolve = resolve })
      : Promise.resolve({ items: ['B'], count: 1 }), scope), { initialProps: { scope: 'A' } })
  rerender({ scope: 'B' })
  await flush()
  await act(async () => { oldResolve({ items: ['A'], count: 1 }) })
  expect(result.current.items).toEqual(['B'])
})
