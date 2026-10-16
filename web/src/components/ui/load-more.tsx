// Copied from mollymogi/mogi-ui packages/ui/src/components/ui/load-more.tsx @ 4998ed1 (read-only source).
import * as React from "react"
import { LoaderCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

// mogi: 列表底部的"已载入 N 条 + 继续加载"。不叫 Pagination，避免和 shadcn 的页码分页组件撞名。
function LoadMore({
  className,
  loaded,
  pages,
  hasMore,
  loading = false,
  onLoadMore,
  status,
  loadMoreLabel = "继续加载",
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  /** 已载入条数 */
  loaded: number
  /** 已载入页数，可选 */
  pages?: number
  hasMore: boolean
  loading?: boolean
  onLoadMore?: () => void
  /** 自定义说明文字，传入后替换默认文案 */
  status?: React.ReactNode
  loadMoreLabel?: React.ReactNode
}) {
  const defaultStatus = (
    <>
      已载入 {loaded} 条{pages !== undefined ? `（${pages} 页）` : ""}，
      {hasMore ? "还有更多" : "已全部载入"}
    </>
  )

  return (
    <div
      data-slot="load-more"
      data-state={loading ? "loading" : hasMore ? "more" : "done"}
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 py-3 text-sm",
        className
      )}
      {...props}
    >
      <p
        data-slot="load-more-status"
        role="status"
        aria-live="polite"
        className="text-muted-foreground"
      >
        {status ?? defaultStatus}
      </p>
      {hasMore && (
        <Button
          data-slot="load-more-button"
          variant="outline"
          size="sm"
          onClick={onLoadMore}
          disabled={loading}
          aria-busy={loading}
        >
          {loading && <LoaderCircle className="animate-spin" />}
          {loadMoreLabel}
        </Button>
      )}
    </div>
  )
}

export { LoadMore }
