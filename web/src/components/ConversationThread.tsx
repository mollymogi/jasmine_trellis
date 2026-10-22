import type { ReactNode } from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, RefreshCw } from 'lucide-react'
import type { RawEvent } from '../api/types'
import type { CoreApiError } from '../api/errors'
import { formatTimestamp } from '../lib/text'
import { ConversationMessage } from './ConversationMessage'
import { EventRow } from './EventRow'
import { Empty, ErrorPanel, Loading } from './States'
import { Button } from './ui/button'

/** The parent keys this reader by scope. Scroll only this viewport; scrolling
 * an ancestor into view would move the entire Dashboard on every poll.
 */
export function ConversationThread({ messages, systemEvents, taskId, loading, error, loadedAt, onReload, sessionPicker }: {
  sessionPicker?: ReactNode
  messages: RawEvent[]
  systemEvents: RawEvent[]
  taskId: string | null
  loading: boolean
  error: CoreApiError | null
  loadedAt: string | null
  onReload: () => void
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLElement>(null)
  const list = useRef<HTMLOListElement>(null)
  const following = useRef(true)
  const previousCount = useRef(0)
  const [atLatest, setAtLatest] = useState(true)
  const [unread, setUnread] = useState(0)
  const lastId = messages.at(-1)?.event_id

  const fitWindow = useCallback(() => {
    const node = panel.current
    if (node) node.style.setProperty('--chat-top', `${Math.round(node.getBoundingClientRect().top + window.scrollY)}px`)
  }, [])
  // Project/filter rows can change height as data arrives. Use document offset
  // so scrolling the outer page never makes this panel grow and chase it.
  useLayoutEffect(fitWindow)
  useEffect(() => {
    window.addEventListener('resize', fitWindow)
    let active = true
    void document.fonts?.ready.then(() => { if (active) fitWindow() })
    return () => { active = false; window.removeEventListener('resize', fitWindow) }
  }, [fitWindow])

  const jumpLatest = useCallback(() => {
    if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight
    following.current = true
    setAtLatest(true)
    setUnread(0)
  }, [])
  const pause = useCallback(() => {
    following.current = false
    setAtLatest(false)
  }, [])

  useLayoutEffect(() => {
    if (following.current) jumpLatest()
    else setUnread(value => value + Math.max(0, messages.length - previousCount.current))
    previousCount.current = messages.length
  }, [messages.length, lastId, jumpLatest])

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      fitWindow()
      if (following.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight
    })
    if (viewport.current) observer.observe(viewport.current)
    if (list.current) observer.observe(list.current)
    if (panel.current?.parentElement?.parentElement) observer.observe(panel.current.parentElement.parentElement)
    return () => observer.disconnect()
  }, [fitWindow])

  return (
    <section ref={panel} className="panel conversation-panel" aria-label={`对话（${messages.length} 条消息）`}>
      <header className="panel__head">
        <div className="chat-heading"><h2>对话</h2>{sessionPicker}<span>{messages.length} 条消息</span></div>
        <Button type="button" variant="ghost" size="sm" onClick={onReload} disabled={loading}>
          <RefreshCw size={14} aria-hidden="true" />重新读取
        </Button>
      </header>
      <div className="chat-window">
        <div ref={viewport} className="chat-scroll" role="region" aria-label="对话消息" tabIndex={0}
          onScroll={event => {
            const node = event.currentTarget
            const bottom = node.scrollHeight - node.clientHeight - node.scrollTop <= 48
            following.current = bottom
            setAtLatest(bottom)
            if (bottom) setUnread(0)
          }}>
          {loading && messages.length === 0 ? <Loading what="对话" /> : null}
          {error ? <ErrorPanel error={error} what="对话" onRetry={onReload} /> : null}
          {!loading && !error && messages.length === 0 ? <Empty what="对话消息" hint="该范围尚无已采集的用户或助手消息。" /> : null}
          <ol ref={list} className="chat-messages" aria-label="用户和助手对话">
            {messages.map((event, index) => {
              const date = new Date(event.occurred_at)
              const previous = messages[index - 1]
              const day = date.toLocaleDateString()
              const showDate = !previous || day !== new Date(previous.occurred_at).toLocaleDateString()
              return <ConversationMessage key={event.event_id} event={event} taskId={taskId} onOpenSource={pause}
                dateLabel={showDate ? date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }) : undefined} />
            })}
          </ol>
        </div>
        {!atLatest && messages.length > 0 ? (
          <Button type="button" variant="secondary" size="sm" className="chat-jump" onClick={jumpLatest}>
            <ArrowDown size={15} aria-hidden="true" />{unread > 0 ? `${unread} 条新消息 · 回到最新` : '回到最新消息'}
          </Button>
        ) : null}
      </div>
      <footer className="chat-footer">
        <p role="status">
          <span className="chat-sync-dot" aria-hidden="true" />
          {error ? '同步失败，保留已有消息；稍后自动重试' : loading ? '正在同步…' : atLatest ? '已定位最新 · 每 3 秒同步' : '正在查看历史 · 向下滚动回到最新'}
          {loadedAt ? <time dateTime={loadedAt} title={formatTimestamp(loadedAt)}>上次同步 {new Date(loadedAt).toLocaleTimeString('zh-CN', { hour12: false })}</time> : null}
        </p>
        <span>助手回复完成后显示</span>
      </footer>
      <details className="system-events chat-system-events">
        <summary>系统事件与来源（{systemEvents.length} 条）</summary>
        <ol className="events">{systemEvents.map(event => <EventRow key={event.event_id} event={event} taskId={taskId} />)}</ol>
      </details>
    </section>
  )
}
