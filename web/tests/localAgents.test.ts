// @vitest-environment node
import http from 'node:http'
import { once } from 'node:events'
import { afterEach, expect, it, vi } from 'vitest'
import { discoverAgents, serveLocalAgents } from '../server/local-agents.mjs'

const servers: http.Server[] = []
afterEach(async () => { await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()) }))) })
async function listen(handler: http.RequestListener) {
  const server = http.createServer(handler); servers.push(server); server.listen(0, '127.0.0.1'); await once(server, 'listening')
  return new URL(`http://127.0.0.1:${(server.address() as {port:number}).port}`)
}
it('detects fixed CLI and app paths without treating installation as capture support', () => {
  const stat = vi.fn((path: string) => {
    if (path !== '/opt/homebrew/bin/codex' && path !== '/Applications/Claude.app' && path !== '/home/test/.opencode/bin/opencode') throw Error('missing')
    return { isDirectory: () => path.endsWith('.app'), isFile: () => !path.endsWith('.app'), mode: 0o755 }
  })
  const agents = discoverAgents({ home:'/home/test', envPath:'/opt/homebrew/bin:relative', stat })
  expect(agents.find(agent => agent.id === 'codex')).toMatchObject({ cli:true, desktop:false, capture_supported:true })
  expect(agents.find(agent => agent.id === 'claude')).toMatchObject({ cli:false, desktop:true, capture_supported:false })
  expect(agents.find(agent => agent.id === 'opencode')?.detected).toBe(true)
  expect(agents.find(agent => agent.id === 'grok')?.detected).toBe(false)
  expect(JSON.stringify(agents)).not.toContain('/home/test')
  expect(stat.mock.calls.every(([path]) => path.startsWith('/'))).toBe(true)
})
it('requires a same-origin authenticated objects read before scanning and never forwards caller targets', async () => {
  const reads: string[] = []
  const core = await listen((req, res) => { reads.push(req.url!); res.writeHead(req.headers.authorization === 'Bearer valid' ? 200 : 401); res.end('{}') })
  const discover = vi.fn(() => discoverAgents({ stat: () => { throw Error('missing') } }))
  const host = await listen((req, res) => { void serveLocalAgents(req, res, core, discover) })
  expect((await fetch(host)).status).toBe(401)
  expect((await fetch(host, {headers:{Authorization:'Bearer bad'}})).status).toBe(401)
  expect((await fetch(host, {headers:{Authorization:'Bearer valid', Origin:'http://evil.invalid'}})).status).toBe(403)
  const wrongHost = await new Promise<number>(resolve => { http.get(host, {headers:{Authorization:'Bearer valid', Host:'evil.invalid'}}, response => { response.resume();resolve(response.statusCode!) }) })
  expect(wrongHost).toBe(403)
  expect((await fetch(host, {method:'POST',headers:{Authorization:'Bearer valid'}})).status).toBe(405)
  expect(discover).not.toHaveBeenCalled()
  const result = await fetch(new URL('/?target=http://evil.invalid',host),{headers:{Authorization:'Bearer valid'}})
  expect(result.status).toBe(200); expect((await result.json()).scope).toBe('dashboard_host_known_locations')
  expect(reads).toEqual(['/v1/projects?limit=1', '/v1/projects?limit=1'])
  expect(discover).toHaveBeenCalledTimes(1)
})
