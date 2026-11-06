/**
 * Local auto sign-in: the dashboard host hands its own page the operator's
 * browser credential (`GET /_local/session`), and the page signs in by itself.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { chmodSync, mkdtempSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../src/App'
import { SessionProvider } from '../src/app/session'
import { isOwnPage, readLocalCredential, serveLocalSession } from '../server/local-session.mjs'
import { installFakeCore, type FakeCore } from './helpers/fakeCore'

let fake: FakeCore | null = null
afterEach(() => { fake?.restore(); fake = null; vi.restoreAllMocks() })

const TOKEN = 'tok_local_operator_0123456789'
const PROJECTS = { body: { projects: [], count: 0 } }

function credential(mode = 0o600, body: unknown = { token: TOKEN }): string {
  const dir = mkdtempSync(join(tmpdir(), 'local-signin-'))
  const file = join(dir, 'browser-daily.json')
  writeFileSync(file, JSON.stringify(body))
  chmodSync(file, mode)
  return file
}

function call(headers: Record<string, string>, env: Record<string, string | undefined>, method = 'GET') {
  const res = { statusCode: 0, headers: {} as Record<string, string>, body: '',
    setHeader(name: string, value: string) { this.headers[name] = value },
    end(body: string) { this.body = body } }
  serveLocalSession({ method, headers } as never, res as never, { port: 15180, env })
  return { status: res.statusCode, body: res.body ? JSON.parse(res.body) : null, headers: res.headers }
}

const OWN = { host: '127.0.0.1:15180', 'sec-fetch-site': 'same-origin' }

describe('GET /_local/session', () => {
  it('hands the token only to this server’s own page', () => {
    const env = { P6_LOCAL_SIGNIN_CREDENTIAL: credential() }
    const ok = call(OWN, env)
    expect(ok.status).toBe(200)
    expect(ok.body).toEqual({ token: TOKEN })
    expect(ok.headers['cache-control']).toBe('no-store')
    expect(ok.headers['access-control-allow-origin']).toBeUndefined()
    // A cross-site page, a DNS-rebinding host, another port, or a foreign Origin get nothing.
    expect(call({ ...OWN, 'sec-fetch-site': 'cross-site' }, env).status).toBe(403)
    expect(call({ host: '127.0.0.1:15180' }, env).status).toBe(403)
    expect(call({ ...OWN, host: 'evil.example:15180' }, env).status).toBe(403)
    expect(call({ ...OWN, host: '127.0.0.1:9999' }, env).status).toBe(403)
    expect(call({ ...OWN, origin: 'http://evil.example' }, env).status).toBe(403)
    expect(call(OWN, env, 'POST').status).toBe(405)
  })

  it('is off unless the operator configures it, and refuses a credential that is not private', () => {
    expect(call(OWN, {}).status).toBe(404)
    expect(call(OWN, { P6_LOCAL_SIGNIN_CREDENTIAL: credential(0o644) }).status).toBe(503)
    expect(call(OWN, { P6_LOCAL_SIGNIN_CREDENTIAL: credential(0o600, { token: 'short' }) }).status).toBe(503)
    const real = credential()
    const link = join(mkdtempSync(join(tmpdir(), 'local-signin-link-')), 'link.json')
    symlinkSync(real, link)
    expect(readLocalCredential(link)).toBeNull()
    expect(readLocalCredential('relative.json')).toBeNull()
    expect(isOwnPage({ headers: { ...OWN, host: 'localhost:15180', origin: 'http://localhost:15180' } } as never, 15180)).toBe(true)
  })
})

describe('the page signs in by itself', () => {
  it('uses the local credential after an authenticated read, without the token form', async () => {
    fake = installFakeCore({ 'GET /_local/session': { body: { token: TOKEN } }, 'GET /v1/projects': PROJECTS })
    render(<SessionProvider localSignIn><App /></SessionProvider>)
    expect(await screen.findByRole('navigation', { name: '主导航' })).toBeTruthy()
    expect(screen.queryByLabelText('Core API token')).toBeNull()
    expect(fake.callsTo('GET', '/v1/projects')[0]?.authorization).toBe('Bearer ' + TOKEN)
  })

  it('falls back to the token form when the host does not offer it or Core refuses the token', async () => {
    fake = installFakeCore({ 'GET /_local/session': { status: 404, body: { error: 'local_signin_disabled' } } })
    render(<SessionProvider localSignIn><App /></SessionProvider>)
    expect(await screen.findByLabelText('Core API token')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '本机自动登录' })).toBeNull()
  })

  it('stays signed out after 退出 and offers the local sign-in as a button', async () => {
    const user = userEvent.setup()
    fake = installFakeCore({ 'GET /_local/session': { body: { token: TOKEN } }, 'GET /v1/projects': PROJECTS })
    render(<SessionProvider localSignIn><App /></SessionProvider>)
    await user.click(await screen.findByRole('button', { name: /账户与设置/ }))
    await user.click(await screen.findByRole('menuitem', { name: '退出登录' }))
    const again = await screen.findByRole('button', { name: '本机自动登录' })
    expect(screen.getByLabelText('Core API token')).toBeTruthy()
    await user.click(again)
    expect(await screen.findByRole('navigation', { name: '主导航' })).toBeTruthy()
  })
})
