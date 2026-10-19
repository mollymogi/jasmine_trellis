import type { ReactNode } from 'react'

/** Native disclosure: keyboard accessible, closed until the reader asks. */
export function Disclosure({ title, children, testId }: { title: string; children: ReactNode; testId?: string }): JSX.Element {
  return (
    <details className="rounded-lg border p-3" data-testid={testId}>
      <summary className="cursor-pointer font-medium">{title}</summary>
      <div className="stack mt-3">{children}</div>
    </details>
  )
}
