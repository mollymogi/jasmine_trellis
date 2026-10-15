// Copied from mollymogi/mogi-ui packages/ui/src/components/ui/description-list.tsx @ 4998ed1 (read-only source).
import * as React from "react"
import { cn } from "@/lib/utils"

// mogi: 键值列表，渲染为语义化的 dl > div > dt + dd。
// 两列网格：字段名列宽随内容，值列自动换行；compact 收紧行距。
function DescriptionList({
  className,
  compact = false,
  ...props
}: React.ComponentProps<"dl"> & { compact?: boolean }) {
  return (
    <dl
      data-slot="description-list"
      data-compact={compact || undefined}
      className={cn(
        "grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 text-sm",
        compact ? "gap-y-1" : "gap-y-3",
        className
      )}
      {...props}
    />
  )
}

function DescriptionItem({
  className,
  term,
  children,
  ...props
}: React.ComponentProps<"div"> & { term: React.ReactNode }) {
  return (
    <div
      data-slot="description-item"
      className={cn("col-span-2 grid grid-cols-subgrid items-baseline", className)}
      {...props}
    >
      <dt data-slot="description-term" className="text-muted-foreground">
        {term}
      </dt>
      <dd data-slot="description-details" className="min-w-0 break-words text-foreground">
        {children}
      </dd>
    </div>
  )
}

export { DescriptionList, DescriptionItem }
