import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDashboardServer, isAllowed, loadAllowlist } from '../server/serve.mjs'
const previous = process.env.P6_ENABLE_MEMORY_ENGINE_CONTROL
afterEach(() => { if (previous === undefined) delete process.env.P6_ENABLE_MEMORY_ENGINE_CONTROL; else process.env.P6_ENABLE_MEMORY_ENGINE_CONTROL = previous; vi.resetModules() })
describe('local engine routes on both proxy hosts', () => {
  it.each([undefined, '0', '1', ' yes '])('uses only the explicit engine switch %j', async value => {
    if (value === undefined) delete process.env.P6_ENABLE_MEMORY_ENGINE_CONTROL; else process.env.P6_ENABLE_MEMORY_ENGINE_CONTROL = value
    vi.resetModules(); const dev = await import('../server/core-proxy'); const enabled = value === '1' || value === ' yes '
    const staticList = loadAllowlist(join(process.cwd(), 'server'), { enableMemoryEngineControl: enabled })
    for (const [method, path, allowed] of [['GET', '/v1/memory/engine', enabled], ['POST', '/v1/memory/engine/start', enabled], ['GET', '/v1/memory/engine/start', false], ['POST', '/v1/memory/engine', false], ['POST', '/v1/memory/engine/start/extra', false]] as const) {
      expect(dev.isAllowedCoreRequest(method, path)).toBe(allowed); expect(isAllowed(staticList, method, path)).toBe(allowed)
    }
  })
  it('refuses cross-origin startup before reaching Core', async () => {
    const server = createDashboardServer({ root: '/unused', origin: new URL('http://127.0.0.1:1'), allowlist: loadAllowlist(join(process.cwd(), 'server'), { enableMemoryEngineControl: true }) })
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
    try {
      const addr = server.address(); if (!addr || typeof addr === 'string') throw new Error('no address')
      const result = await fetch(`http://127.0.0.1:${addr.port}/v1/memory/engine/start`, { method: 'POST', headers: { origin: 'http://unrelated.invalid', 'content-type': 'application/json' }, body: '{}' })
      expect(result.status).toBe(403); expect((await result.json()).error.code).toBe('proxy_origin_not_allowed')
    } finally { await new Promise<void>(resolve => server.close(() => resolve())) }
  })
})
