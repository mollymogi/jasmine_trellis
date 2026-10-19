/**
 * The application sidebar (mogi-ui `Sidebar`).
 *
 * Holds the business navigation, the pending-confirmation links and the placeholder list of
 * domains whose contracts are not frozen. The footer is a single account menu: theme choice and
 * sign-out live there, not in a separate header. Nothing here reads or stores the token.
 */

import {
  Brain,
  ChevronsUpDown,
  CircleDashed,
  ClipboardCheck,
  FileSearch,
  LayoutDashboard,
  ListChecks,
  MessagesSquare,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'

import { hrefFor, type Route } from '../app/router'
import type { PendingConfirmation } from '../api/webConfirmations'
import { pendingKeys } from '../views/PendingView'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from '@/components/ui/sidebar'

// Each item carries an icon, so the collapsed icon rail still says what a button opens.
export const NAV: { label: string; href: string; icon: LucideIcon }[] = [
  { label: 'Memory', href: hrefFor({ name: 'memoryItems', memoryId: null }), icon: Brain },
  { label: 'Validation', href: '#/validation/runs', icon: ShieldCheck },
  { label: 'Overview', href: hrefFor({ name: 'overview' }), icon: LayoutDashboard },
  { label: 'Conversation', href: hrefFor({ name: 'conversation', projectId: null, taskId: null, sessionId: null }), icon: MessagesSquare },
  { label: 'Interpretations', href: hrefFor({ name: 'interpretations', projectId: null, taskId: null, eventId: null, status: null }), icon: FileSearch },
  { label: '待审批 Review', href: hrefFor({ name: 'reviews', projectId: null, taskId: null }), icon: ClipboardCheck },
  { label: 'Rules', href: hrefFor({ name: 'rules', projectId: null, taskId: null, ruleId: null }), icon: ListChecks },
]

/** Decorative or brand text: removed from view when the sidebar is collapsed to its icon rail. */
const LABEL = 'group-data-[collapsible=icon]:hidden'
/**
 * Link text: hidden on screen when collapsed, but kept for assistive technology, so a collapsed
 * link still has an accessible name (`display: none` would remove it from the accessibility tree).
 */
const LINK_LABEL = 'group-data-[collapsible=icon]:sr-only'

export function isCurrent(route: Route, href: string): boolean {
  if (href.endsWith('/overview')) return route.name === 'overview'
  if (href.includes('/conversation')) return route.name === 'conversation'
  if (href.includes('/interpretations')) return route.name === 'interpretations'
  if (href.includes('/reviews')) return route.name === 'reviews'
  if (href.includes('/validation')) return route.name.startsWith('validation')
  if (href.includes('/memory')) return route.name.startsWith('memory')
  if (href.includes('/rules')) return route.name === 'rules'
  return false
}

export function AppSidebar({
  route,
  pendingConfirmations,
  dark,
  onToggleTheme,
  onSignOut,
}: {
  route: Route
  pendingConfirmations: PendingConfirmation[]
  dark: boolean
  onToggleTheme: () => void
  onSignOut: () => void
}): JSX.Element {
  const { isMobile, setOpenMobile } = useSidebar()
  // On a phone the sidebar is a modal sheet over the page: once a link is chosen, close it so the
  // target view is not left covered with focus trapped in the sheet.
  const closeOnMobile = (): void => {
    if (isMobile) setOpenMobile(false)
  }
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="px-2 py-1">
          <p className={`font-semibold ${LABEL}`}>Jasmine Core</p>
          <p className={`text-xs text-muted-foreground ${LABEL}`}>本机可用版</p>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>导航</SidebarGroupLabel>
          <SidebarGroupContent>
            <nav aria-label="主导航">
              <SidebarMenu>
                {NAV.map((item) => (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton asChild isActive={isCurrent(route, item.href)} tooltip={item.label}>
                      <a href={item.href} onClick={closeOnMobile} aria-current={isCurrent(route, item.href) ? 'page' : undefined}>
                        <item.icon />
                        <span className={LINK_LABEL}>{item.label}</span>
                      </a>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </nav>
          </SidebarGroupContent>
        </SidebarGroup>

        {pendingConfirmations.length > 0 ? (
          <SidebarGroup>
            <SidebarGroupLabel>待恢复确认</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {pendingConfirmations.map((pending) => (
                  <SidebarMenuItem key={pending.id}>
                    <SidebarMenuButton asChild tooltip={`恢复未知确认 ${pending.id}`}>
                      <a href={`#/task/${pending.taskId}/confirmation`} onClick={closeOnMobile}>
                        <CircleDashed />
                        <span className={LINK_LABEL}>恢复未知确认 {pending.id}</span>
                      </a>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ) : null}

        <SidebarGroup>
          <SidebarGroupLabel>尚未开放 / 使用范围</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {pendingKeys().map((key) => (
                <SidebarMenuItem key={key}>
                  <SidebarMenuButton asChild tooltip={key}>
                    <a href={hrefFor({ name: 'pending', key })} onClick={closeOnMobile}>
                      <CircleDashed />
                      <span className={LINK_LABEL}>{key}</span>
                    </a>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <AccountMenu dark={dark} onToggleTheme={onToggleTheme} onSignOut={onSignOut} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function AccountMenu({
  dark,
  onToggleTheme,
  onSignOut,
}: {
  dark: boolean
  onToggleTheme: () => void
  onSignOut: () => void
}): JSX.Element {
  const { isMobile, state } = useSidebar()
  // Opens upward in the full sidebar, and to the right when the sidebar is collapsed to icons.
  const side = state === 'collapsed' && !isMobile ? 'right' : 'top'
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" tooltip="账户与设置">
              <Avatar className="size-8 rounded-lg">
                <AvatarFallback className="rounded-lg">CM</AvatarFallback>
              </Avatar>
              <div className="grid flex-1 text-left leading-tight group-data-[collapsible=icon]:sr-only">
                <span className="truncate font-medium">Core 已连接</span>
                <span className="truncate text-xs text-muted-foreground">token 仅在内存中</span>
              </div>
              <ChevronsUpDown className={`ml-auto ${LABEL}`} />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent side={side} align="start" className="min-w-56">
            <DropdownMenuItem onSelect={onToggleTheme}>{dark ? '切换到浅色主题' : '切换到深色主题'}</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onSignOut}>退出(清除内存中的 token)</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
