// Copied from mollymogi/mogi-ui packages/ui/src/components/ui/detail-layout.tsx @ bcd41e15a648dfa631a11177d8fdda75ef0dddff (read-only source; candidate PR).
import * as React from "react"
import { cn } from "@/lib/utils"

// mogi: 详情看板的信息层级；业务内容仍由 Card、Badge、Sheet 等已有组件组合。
// 首屏呈现结论和指标，宽屏主区/辅助栏分开，窄屏按阅读顺序排列。
type DetailLayoutProps = React.ComponentProps<"div"> & {
  header?: React.ReactNode
  summary?: React.ReactNode
  metrics?: React.ReactNode
  aside?: React.ReactNode
  metricColumns?: 2 | 3 | 4
}

function DetailLayout({
  className,
  header,
  summary,
  metrics,
  aside,
  metricColumns = 3,
  children,
  ...props
}: DetailLayoutProps) {
  const hasAside = aside != null && aside !== false
  const metricGrid = {
    2: "sm:grid-cols-2",
    3: "sm:grid-cols-3",
    4: "sm:grid-cols-2 xl:grid-cols-4",
  }[metricColumns]

  return (
    <div data-slot="detail-layout" className={cn("grid grid-cols-1 min-w-0 gap-6", className)} {...props}>
      {header != null && <div data-slot="detail-layout-header" className="min-w-0">{header}</div>}
      {summary != null && <div data-slot="detail-layout-summary" className="min-w-0">{summary}</div>}
      {metrics != null && (
        <div data-slot="detail-layout-metrics" className={cn("grid grid-cols-1 min-w-0 gap-4", metricGrid)}>
          {metrics}
        </div>
      )}
      <div data-slot="detail-layout-body" className={cn("grid grid-cols-1 min-w-0 items-start gap-6", hasAside && "lg:grid-cols-3")}>
        <div data-slot="detail-layout-main" className={cn("grid grid-cols-1 min-w-0 gap-6", hasAside && "lg:col-span-2")}>
          {children}
        </div>
        {hasAside && <aside data-slot="detail-layout-aside" className="grid grid-cols-1 min-w-0 gap-4">{aside}</aside>}
      </div>
    </div>
  )
}

export { DetailLayout }
export type { DetailLayoutProps }
