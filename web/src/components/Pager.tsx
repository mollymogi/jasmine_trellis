/**
 * Pagination footer for every paged list (P6-A07).
 *
 * The wording is part of the contract: `count` in a Core list response is the
 * length of *that page*, so the UI states how many rows it has loaded and
 * refuses to imply a total. "已读完" appears only when a short page proves it.
 */

import type { Paged } from '../app/usePagedResource'
import { LoadMore } from '@/components/ui/load-more'
import { ErrorPanel } from './States'

export function Pager({
  paged,
  noun,
  limit,
  cursorKind = 'offset',
}: {
  paged: Paged<unknown>
  noun: string
  limit: number
  /**
   * `offset`/`after_seq` prove completion by a short page; `after_id` by Core's null
   * cursor; `has_more` by Core's own `has_more` flag (the Validation list computes
   * `offset + count < total`, so a full last page is still the last page).
   */
  cursorKind?: 'offset' | 'after_seq' | 'after_id' | 'has_more'
}): JSX.Element {
  const completeReason =
    cursorKind === 'after_id'
      ? 'Core 对 after_id 返回了 null 游标(next_after_id 为空)。'
      : cursorKind === 'has_more'
        ? 'Core 在最后一页返回 has_more = false。'
        : '最后一页短于 limit。'
  return (
    <div className="pager">
      {paged.error && paged.items.length > 0 ? (
        // Only a failure that happened *after* rows were already shown belongs
        // here; a failed first page is the view's own error panel, and rendering
        // both would show the same refusal twice.
        <ErrorPanel error={paged.error} what={noun} onRetry={paged.reload} />
      ) : paged.error ? null : (
        <LoadMore
          loaded={paged.items.length}
          pages={paged.pages}
          hasMore={paged.hasMore}
          loading={paged.loading}
          onLoadMore={paged.loadMore}
          loadMoreLabel={`继续加载${noun}`}
          status={
            <>
              已载入 {paged.items.length} 条{noun} · {paged.pages} 页(每页 {limit};
              <code>count</code> 是本页长度,不是总数)。
              {paged.complete ? ` ${completeReason}` : ' 是否还有更多由 Core 决定。'}
            </>
          }
        />
      )}
    </div>
  )
}
