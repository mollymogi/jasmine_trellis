/**
 * The application sidebar (mogi-ui `Sidebar`).
 *
 * Holds business navigation and pending-confirmation links. Feature explanations have one
 * help entry. The footer is a single account menu: theme choice and
 * sign-out live there, not in a separate header. Nothing here reads or stores the token.
 */

import {
  Brain,
  ChevronsUpDown,
  CircleDashed,
  ClipboardCheck,
  FileSearch,
  Flower2,
  HelpCircle,
  LayoutDashboard,
  ListChecks,
  LogOut,
  MessagesSquare,
  Moon,
  PawPrint,
  ShieldCheck,
  Sun,
  type LucideIcon,
} from 'lucide-react'

import { hrefFor, type Route } from '../app/router'
import type { PendingConfirmation } from '../api/webConfirmations'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
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
  { label: 'Overview', href: hrefFor({ name: 'overview' }), icon: LayoutDashboard },
  { label: 'Conversation', href: hrefFor({ name: 'conversation', projectId: null, taskId: null, sessionId: null }), icon: MessagesSquare },
  { label: 'Memory', href: hrefFor({ name: 'memoryHealth' }), icon: Brain },
  { label: 'Rules', href: hrefFor({ name: 'rules', projectId: null, taskId: null, ruleId: null }), icon: ListChecks },
  { label: 'Interpretations', href: hrefFor({ name: 'interpretations', projectId: null, taskId: null, eventId: null, status: null }), icon: FileSearch },
  { label: '待审批 Review', href: hrefFor({ name: 'reviews', projectId: null, taskId: null }), icon: ClipboardCheck },
  { label: 'Validation', href: '#/validation/runs', icon: ShieldCheck },
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
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" tooltip="Jasmine Core 看板">
              <a href={hrefFor({ name: 'memoryHealth' })} onClick={closeOnMobile} aria-label="Jasmine Core 看板">
                <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground"><PawPrint className="size-4" /></div>
                <div className={`grid text-left leading-tight ${LABEL}`}>
                  <span className="font-bold">Jasmine Core</span>
                  <span className="text-xs text-muted-foreground">工作看板</span>
                </div>
              </a>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
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
          <SidebarGroupLabel>帮助</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={route.name === 'pending'} tooltip="功能说明">
                  <a href={hrefFor({ name: 'pending', key: 'features' })} onClick={closeOnMobile} aria-current={route.name === 'pending' ? 'page' : undefined}>
                    <HelpCircle />
                    <span className={LINK_LABEL}>功能说明</span>
                  </a>
                </SidebarMenuButton>
              </SidebarMenuItem>
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
  const avatar = <Avatar className="size-8"><AvatarFallback className="bg-primary/10 text-primary"><Flower2 className="size-4" /></AvatarFallback></Avatar>
  const identity = <div className="grid min-w-0 flex-1 text-left leading-tight"><span className="truncate font-bold">本机会话</span><span className="truncate text-xs text-muted-foreground">Core 已连接</span></div>
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" tooltip="账户与设置" aria-label="本机会话 · Core 已连接 · 账户与设置" className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground">
              {avatar}
              <div className={`min-w-0 flex-1 ${LINK_LABEL}`}>{identity}</div>
              <ChevronsUpDown className={`ml-auto ${LABEL}`} />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent side={side} align={side === 'right' ? 'end' : 'start'} sideOffset={6} className="w-(--radix-dropdown-menu-trigger-width) min-w-56">
            <DropdownMenuLabel className="flex items-center gap-2 font-normal">{avatar}{identity}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onToggleTheme}>{dark ? <Sun /> : <Moon />}{dark ? '切换到浅色主题' : '切换到深色主题'}</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={onSignOut}><LogOut />退出登录</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
