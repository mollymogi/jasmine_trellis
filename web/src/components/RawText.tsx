/**
 * Text renderer for untrusted content.
 *
 * Renders as a React text node — there is no HTML parsing and no
 * `dangerouslySetInnerHTML` anywhere in this bundle. Credential shapes are
 * masked by default; the operator can reveal the exact original of *this* field,
 * and a copy action always copies the masked form.
 */

import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CodeBlock } from '@/components/ui/code-block'
import { clip, hasCredentialShape } from '../lib/text'
import { useMaskedText } from '../lib/useMaskedText'

export function RawText({
  value,
  label,
  maxCollapsed = 480,
  emptyText = '(空原文)',
  machine = false,
}: {
  value: string
  label: string
  maxCollapsed?: number
  emptyText?: string
  machine?: boolean
}): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const masked = useMaskedText(value)
  const clipped = expanded ? masked.shown : clip(masked.shown, maxCollapsed)
  const [copied, setCopied] = useState<'idle' | 'done' | 'blocked'>('idle')

  const copy = async (): Promise<void> => {
    // The clipboard receives the masked summary only; a revealed view is not an
    // export channel.
    try {
      await navigator.clipboard.writeText(masked.masked.text)
      setCopied('done')
    } catch {
      setCopied('blocked')
    }
  }

  const long = masked.shown.length > maxCollapsed

  return (
    <CodeBlock
      label={label}
      masked={masked.hasSecrets && !masked.revealed}
      tags={machine ? [{ label: '机器事件(非用户发言)', variant: 'soft' }] : undefined}
      bodyClassName="rawtext__body"
      actions={
        <>
          {masked.hasSecrets ? (
            <Badge variant="warning" data-testid="masked-note">
              已按默认策略脱敏 {masked.masked.masked.map((item) => item.label).join('、')}
            </Badge>
          ) : null}
          {long ? (
            <Button type="button" variant="outline" size="xs" onClick={() => setExpanded((value) => !value)}>
              {expanded ? '折叠' : '展开全文'}
            </Button>
          ) : null}
          {hasCredentialShape(value) ? (
            <Button type="button" variant="outline" size="xs" onClick={masked.revealed ? masked.hide : masked.reveal}>
              {masked.revealed ? '恢复脱敏' : '显示原文'}
            </Button>
          ) : null}
          <Button type="button" variant="outline" size="xs" onClick={() => void copy()}>
            {copied === 'done' ? '已复制脱敏摘要' : copied === 'blocked' ? '复制被拒绝' : '复制脱敏摘要'}
          </Button>
        </>
      }
    >
      {value.length === 0 ? emptyText : clipped}
    </CodeBlock>
  )
}
