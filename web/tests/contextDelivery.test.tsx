import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ContextDeliveryCard, type Delivery } from '../src/views/ContextDelivery'
import type { ContextPack } from '../src/api/types'
const pack = { context_pack_id: 'ctx_test', source_event_id: 'evt_01M5AG83CGMD7K3YMPQY32ZH3J', rendered_sha256: 'a'.repeat(64) } as ContextPack
const receipt: Delivery = { context_pack_id: pack.context_pack_id, status:'MATCHED_NATIVE_INPUT',
  historical:true, current_use_proved:false, trust_boundary:'operator_owned_native_collector', record:{
    source_event_id:pack.source_event_id, rendered_sha256:pack.rendered_sha256,
    native_thread_id:'01a15503-d91d-7c60-ae02-51bd3b57a56c', native_turn_id:'01a15503-d977-79f1-ba71-44adaac4f164',
    observed_at:'2026-10-19T17:00:00Z', receipt_sha256:'b'.repeat(64), native_rollout_sha256:'c'.repeat(64) } }
describe('Historical Context delivery boundary', () => {
  it('shows one concept, folds identifiers and preserves historical limits', async () => {
    render(<ContextDeliveryCard pack={pack} delivery={receipt} />)
    expect(screen.getByTestId('context-delivery').getAttribute('data-status')).toBe('matched')
    expect(screen.getByText('已记录 Codex 接收凭证')).toBeTruthy()
    const details = screen.getByText('查看接收细节').closest('details')!
    expect(details.open).toBe(false)
    await userEvent.click(screen.getByText('查看接收细节'))
    expect(details.open).toBe(true)
    expect(details.querySelector('a')?.getAttribute('href')).toBe('#/event/'+pack.source_event_id)
    expect(screen.getByText(/不代表当前轮次仍在使用/)).toBeTruthy()
  })
  it.each(['NO_RECORD','UNAVAILABLE'])('does not turn %s into delivery', status => {
    render(<ContextDeliveryCard pack={pack} delivery={{...receipt,status,record:null}} />)
    expect(screen.getByTestId('context-delivery').getAttribute('data-status')).toBe('missing')
    expect(screen.queryByText('已记录 Codex 接收凭证')).toBeNull()
  })
  it.each([
    {...receipt,context_pack_id:'another'}, {...receipt,current_use_proved:true},
    {...receipt,status:'NEW_UNKNOWN_STATUS'}, {...receipt,status:'INVALID_RECORD'},
    {...receipt,record:{...receipt.record!,rendered_sha256:'wrong'}},
    {...receipt,record:{...receipt.record!,source_event_id:'wrong'}},
    {...receipt,record:undefined}, {...receipt,record:{...receipt.record!,observed_at:'broken'}},
  ])('refuses malformed, foreign or unknown evidence', value => {
    render(<ContextDeliveryCard pack={pack} delivery={value as Delivery} />)
    expect(screen.getByTestId('context-delivery').getAttribute('data-status')).toBe('invalid')
    expect(screen.queryByText('已记录 Codex 接收凭证')).toBeNull()
  })
  it('keeps API failure visible instead of saying no receipt exists', () => {
    render(<ContextDeliveryCard pack={pack} delivery={null} failed />)
    expect(screen.getByTestId('context-delivery').getAttribute('data-status')).toBe('error')
    expect(screen.getByText('接收记录暂时无法读取')).toBeTruthy()
  })
})
