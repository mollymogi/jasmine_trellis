/**
 * Token entry (P6-A01).
 *
 * The token is typed into a `type="password"` field with a proper label, is
 * validated by *using* it (a real GET), and is held in memory only. The page
 * states the exact scopes this dashboard asks for and never mints, bootstraps or
 * guesses one. Nothing here writes to storage or the URL.
 */

import { useId, useState } from 'react'

import { CoreClient } from '../api/client'
import { makeEndpoints } from '../api/endpoints'
import { useSession, REQUIRED_READ_SCOPES } from '../app/session'
import { CoreApiError, describeError } from '../api/errors'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { ErrorPanel } from './States'

export function SignIn(): JSX.Element {
  const { signIn } = useSession()
  const [value, setValue] = useState('')
  const [error, setError] = useState<CoreApiError | null>(null)
  const [checking, setChecking] = useState(false)
  const inputId = useId()
  const helpId = useId()

  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    const candidateToken = value.trim()
    if (!candidateToken || checking) return
    setChecking(true)
    setError(null)
    // The token is accepted only after a genuinely *authenticated* read, and the
    // session is not opened until it does.
    //
    // `GET /v1/health` is deliberately public (`handlers.Route(public=True)`) and
    // ignores Authorization, so it cannot validate anything: a bogus token gets
    // 200 from it. `GET /v1/projects?limit=1` needs `objects:read`, which every
    // P6-01 view also needs, so a wrong or under-scoped token is refused here
    // instead of silently opening a dashboard that 401s on every panel.
    try {
      const candidate = makeEndpoints(new CoreClient({ getToken: () => candidateToken }))
      await candidate.listProjects({ limit: 1, offset: 0 })
      signIn(candidateToken)
      setValue('')
    } catch (caught) {
      setError(
        caught instanceof CoreApiError
          ? caught
          : new CoreApiError({
              kind: 'unknown',
              status: null,
              code: 'ui_signin_failed',
              message: String(caught),
            }),
      )
    } finally {
      setChecking(false)
    }
  }

  return (
    <main id="main" className="signin">
      <Card className="gap-4 border-0 bg-transparent p-0 shadow-none">
      <CardHeader className="px-0">
        <CardTitle className="font-sans text-xl">Jasmine Core Dashboard(P6-02)</CardTitle>
        <CardDescription>
        页面通过同源代理读取本机 Core。token 只保存在内存中:刷新或点击“退出”即清除,
        不写入 localStorage/sessionStorage/IndexedDB,不进入 URL、日志或打包产物。
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-0">
      <form onSubmit={(event) => void submit(event)} aria-describedby={helpId}>
        <Label htmlFor={inputId}>Core API token</Label>
        <Input
          id={inputId}
          type="password"
          autoComplete="off"
          spellCheck={false}
          className="min-w-80"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <Button type="submit" disabled={checking || value.trim().length === 0}>
          {checking ? '校验中…' : '使用此 token'}
        </Button>
      </form>
      <p id={helpId} className="muted">
        token 需要的 scope(读切片;写入还需要 interpretations:manage 等):{' '}
        {REQUIRED_READ_SCOPES.map((scope) => (
          <code key={scope}>{scope}</code>
        ))}
        。缺少任一 scope 时,对应视图会明确显示 forbidden,而不会改用更高权限的凭据。
      </p>
      <p className="muted">
        “使用此 token”会先做一次<strong>需要鉴权</strong>的读取(<code>GET /v1/projects?limit=1</code>)
        来校验 token:健康检查是公开端点,用它判断等于没有校验。校验失败不会进入 Dashboard。
      </p>
      <p className="muted">
        本页不会代你铸造 key、不会使用 admin bootstrap,也不会调用 fingerprint 扫描。
      </p>
      {error ? (
        <ErrorPanel error={error} what="鉴权校验(GET /v1/projects)" onRetry={() => setError(null)} />
      ) : null}
      </CardContent>
      </Card>
    </main>
  )
}

export function describeSignInFailure(error: CoreApiError): string {
  return describeError(error)
}
