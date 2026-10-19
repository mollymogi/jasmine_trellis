/**
 * The sidebar on a phone-width viewport.
 *
 * Below 768px the sidebar is a modal sheet over the page. Choosing any link in it must close the
 * sheet, or the target view stays covered and focus stays trapped in the sheet. Each group is
 * checked on its own: the business navigation, the pending-confirmation links and the
 * feature explanation entry.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { PendingConfirmation } from '../src/api/webConfirmations'
import { AppSidebar } from '../src/components/AppSidebar'
import { SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'

const PENDING: PendingConfirmation = {
  id: 'cfp_01JZ0000000000000000000000',
  keyId: 'key_01JZ0000000000000000000000',
  taskId: 'tsk_01JZ0000000000000000000000',
  stepId: null,
  kind: 'prepare',
  body: {} as PendingConfirmation['body'],
}

function renderSidebar(): void {
  render(
    <SidebarProvider>
      <AppSidebar
        route={{ name: 'overview' }}
        pendingConfirmations={[PENDING]}
        dark={false}
        onToggleTheme={() => undefined}
        onSignOut={() => undefined}
      />
      <SidebarTrigger />
    </SidebarProvider>,
  )
}

describe('AppSidebar on a phone-width viewport', () => {
  let width: number

  beforeEach(() => {
    width = window.innerWidth
    window.innerWidth = 500
  })

  afterEach(() => {
    window.innerWidth = width
    window.location.hash = ''
  })

  it.each([
    ['a business navigation link', 'Overview'],
    ['a pending-confirmation link', `恢复未知确认 ${PENDING.id}`],
    ['the feature explanation entry', '功能说明'],
  ])('closes the sheet after choosing %s', async (_group, name) => {
    const user = userEvent.setup()
    renderSidebar()

    await user.click(screen.getByRole('button', { name: '收起或展开侧栏' }))
    const sheet = await screen.findByRole('dialog')

    await user.click(within(sheet).getByRole('link', { name }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})
