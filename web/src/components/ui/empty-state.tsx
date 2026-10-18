// Copied from mollymogi/mogi-ui packages/ui/src/components/ui/empty-state.tsx @ 4998ed1 (read-only source).
import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"

// mogi: 空态 / 失败 / 未接入三种显式状态共用一个结构，靠图标底色、边框和文字区分。
// 说明文字完全由调用方传入，组件不改写。
const emptyStateVariants = cva(
  "flex flex-col items-center justify-center gap-3 rounded-lg border px-6 py-10 text-center text-sm",
  {
    variants: {
      variant: {
        empty: "border-dashed bg-card",
        error: "border-destructive/40 bg-card",
        unavailable: "border-dashed bg-muted/50",
      },
    },
    defaultVariants: { variant: "empty" },
  }
)

const iconVariants = cva(
  "flex size-10 items-center justify-center rounded-full [&>svg]:size-5",
  {
    variants: {
      variant: {
        empty: "bg-accent text-accent-foreground",
        error: "bg-destructive/10 text-destructive",
        unavailable: "bg-muted text-muted-foreground",
      },
    },
    defaultVariants: { variant: "empty" },
  }
)

function EmptyState({
  className,
  variant,
  icon,
  title,
  description,
  action,
  children,
  ...props
}: React.ComponentProps<"div"> &
  VariantProps<typeof emptyStateVariants> & {
    icon?: React.ReactNode
    title?: React.ReactNode
    description?: React.ReactNode
    action?: React.ReactNode
  }) {
  return (
    <div
      data-slot="empty-state"
      data-variant={variant ?? "empty"}
      role={variant === "error" ? "alert" : undefined}
      className={cn(emptyStateVariants({ variant }), className)}
      {...props}
    >
      {icon != null && (
        <div data-slot="empty-state-icon" aria-hidden className={iconVariants({ variant })}>
          {icon}
        </div>
      )}
      {title != null && (
        <p data-slot="empty-state-title" className="font-medium text-foreground">
          {title}
        </p>
      )}
      {description != null && (
        <div data-slot="empty-state-description" className="max-w-prose text-muted-foreground">
          {description}
        </div>
      )}
      {children}
      {action != null && <div data-slot="empty-state-action" className="mt-1">{action}</div>}
    </div>
  )
}

// mogi: 加载态：骨架行 + 可读的文字说明，读屏通过 role="status" 播报。
function LoadingState({
  className,
  label = "加载中…",
  rows = 3,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  label?: React.ReactNode
  rows?: number
}) {
  return (
    <div
      data-slot="loading-state"
      role="status"
      aria-live="polite"
      aria-busy
      className={cn("grid gap-3 py-4", className)}
      {...props}
    >
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton
          key={i}
          aria-hidden
          className={cn("h-4", i === rows - 1 && rows > 1 ? "w-2/3" : "w-full")}
        />
      ))}
      <p data-slot="loading-state-label" className="text-sm text-muted-foreground">
        {label}
      </p>
    </div>
  )
}

export { EmptyState, LoadingState, emptyStateVariants }
