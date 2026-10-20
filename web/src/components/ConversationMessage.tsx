import type { RawEvent } from '../api/types'
import { formatTimestamp, payloadText } from '../lib/text'
import { EventRow } from './EventRow'
import { RawText } from './RawText'

export function isConversationMessage(event: RawEvent): boolean {
  return payloadText(event.payload) !== null && (
    (event.event_type === 'user.prompt' && event.actor_kind === 'human') ||
    event.event_type === 'assistant.message'
  )
}

export function ConversationMessage({ event, taskId }: { event: RawEvent; taskId: string | null }) {
  const user = event.event_type === 'user.prompt'
  return (
    <li className={`chat-message ${user ? 'chat-message--user' : 'chat-message--assistant'}`}
      data-message-id={event.event_id} data-seq={event.seq}>
      <div className="chat-message__head">
        <strong>{user ? '用户' : '助手'}</strong>
        <time dateTime={event.occurred_at} title={event.occurred_at}>{formatTimestamp(event.occurred_at)}</time>
      </div>
      <RawText value={payloadText(event.payload) ?? ''} label={user ? '用户消息' : '助手回复'} maxCollapsed={Number.POSITIVE_INFINITY} />
      <details className="chat-message__source">
        <summary>来源详情</summary>
        <ol className="events"><EventRow event={event} taskId={taskId} /></ol>
      </details>
    </li>
  )
}
