// Copied from mollymogi/mogi-ui packages/ui/src/components/ui/code-block.tsx @ 4998ed1 (read-only source).
import * as React from "react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

type BadgeVariant = NonNullable<React.ComponentProps<typeof Badge>["variant"]>

// mogi: 原始 JSON / 文本块。标题栏放标签和操作，内容区限高滚动。
// masked 时自动加"已遮罩"标签和左侧 warning 色条，不只靠颜色区分。
function CodeBlock({
  className,
  label,
  tags,
  actions,
  masked = false,
  maskedLabel = "已遮罩",
  bodyClassName,
  children,
  ...props
}: React.ComponentProps<"figure"> & {
  label?: React.ReactNode
  /** 标题栏标签，variant 沿用 Badge 的变体名 */
  tags?: { label: React.ReactNode; variant?: BadgeVariant }[]
  actions?: React.ReactNode
  masked?: boolean
  maskedLabel?: React.ReactNode
  /** 内容区类名，默认最大高度 max-h-80，可用它覆盖 */
  bodyClassName?: string
}) {
  const hasHeader = label != null || masked || (tags?.length ?? 0) > 0 || actions != null

  return (
    <figure
      data-slot="code-block"
      data-masked={masked || undefined}
      className={cn(
        "overflow-hidden rounded-lg border bg-card text-card-foreground",
        masked && "shadow-[inset_3px_0_0_var(--warning)]",
        className
      )}
      {...props}
    >
      {hasHeader && (
        <figcaption
          data-slot="code-block-header"
          className="flex flex-wrap items-center gap-2 border-b bg-muted/50 px-3 py-2 text-xs"
        >
          {label != null && <span className="font-medium text-foreground">{label}</span>}
          {masked && <Badge variant="warning">{maskedLabel}</Badge>}
          {tags?.map((tag, i) => (
            <Badge key={i} variant={tag.variant ?? "soft"}>
              {tag.label}
            </Badge>
          ))}
          {actions != null && <div className="ml-auto flex items-center gap-2">{actions}</div>}
        </figcaption>
      )}
      <pre
        data-slot="code-block-body"
        // 可滚动区域需要能用键盘聚焦
        tabIndex={0}
        className={cn(
          "max-h-80 overflow-auto p-3 font-mono text-xs leading-relaxed whitespace-pre text-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
          bodyClassName
        )}
      >
        {children}
      </pre>
    </figure>
  )
}

export { CodeBlock }
