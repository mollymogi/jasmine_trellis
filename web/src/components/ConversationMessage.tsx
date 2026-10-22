import type { RawEvent } from '../api/types'
import { Asterisk, Bot, Orbit, SquareTerminal, UserRound } from 'lucide-react'
import { agentIdentity } from '../lib/agentIdentity'
import { formatTimestamp, payloadText } from '../lib/text'
import { EventRow } from './EventRow'
import { RawText } from './RawText'

export function isConversationMessage(event: RawEvent): boolean {
  return payloadText(event.payload) !== null && (
    (event.event_type === 'user.prompt' && event.actor_kind === 'human') ||
    event.event_type === 'assistant.message'
  )
}

export function ConversationMessage({ event, taskId, onOpenSource, dateLabel }: {
  event: RawEvent; taskId: string | null; onOpenSource?: () => void; dateLabel?: string
}) {
  const user = event.event_type === 'user.prompt'
  const agent = agentIdentity(event)
  const Icon = user ? UserRound : agent.kind === 'codex' ? SquareTerminal
    : agent.kind === 'claude' ? Asterisk : agent.kind === 'grok' ? Orbit : Bot
  const name = user ? '你' : agent.label
  const date = new Date(event.occurred_at)
  const time = Number.isNaN(date.getTime()) ? event.occurred_at : new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(date)
  return (
    <li className={`chat-message ${user ? 'chat-message--user' : 'chat-message--assistant'}`}
      data-message-id={event.event_id} data-seq={event.seq}>
      {dateLabel ? <div className="chat-date">{dateLabel}</div> : null}
      <span className={`chat-avatar chat-avatar--${user ? 'user' : agent.kind}`} role="img" aria-label={`${name}头像`}>
        <Icon size={19} aria-hidden="true" />
      </span>
      <div className="chat-message__content">
        <div className="chat-message__head">
          <strong>{name}</strong>
          <time dateTime={event.occurred_at} title={formatTimestamp(event.occurred_at)}>{time}</time>
        </div>
        <RawText appearance="chat" value={payloadText(event.payload) ?? ''} label={user ? '用户消息' : '助手回复'} maxCollapsed={Number.POSITIVE_INFINITY} />
      <details className="chat-message__source" onToggle={event => {
        if (event.currentTarget.open) onOpenSource?.()
      }}>
        <summary>来源详情</summary>
        <ol className="events"><EventRow event={event} taskId={taskId} /></ol>
      </details>
      </div>
    </li>
  )
}
