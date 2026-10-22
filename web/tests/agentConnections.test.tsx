import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AgentConnections } from '../src/components/AgentConnections'
import { SessionProvider } from '../src/app/session'
afterEach(() => vi.unstubAllGlobals())
it('discovers on opening and keeps installed clients distinct from supported capture', async () => {
 const fetcher=vi.fn<(input: string, init?: RequestInit) => Promise<unknown>>().mockResolvedValue({ok:true,json:async()=>({agents:[
  {id:'opencode',name:'OpenCode',cli:true,desktop:false,detected:true,capture_supported:false},
  {id:'codex',name:'Codex',cli:true,desktop:false,detected:true,capture_supported:true},
 ]})});vi.stubGlobal('fetch',fetcher)
 render(<SessionProvider><AgentConnections /></SessionProvider>)
 expect(fetcher).not.toHaveBeenCalled()
 fireEvent.click(screen.getByRole('button',{name:'Agent 接入'}))
 await waitFor(()=>expect(screen.getByText('检测到 2 个客户端')).toBeTruthy())
 expect(screen.getByText('OpenCode').closest('li')?.textContent).toContain('暂不支持自动接入')
 expect(screen.getByText('Codex').closest('li')?.textContent).toContain('已有采集适配器')
 expect(fetcher.mock.calls[0]?.[0]).toBe('/_local/agents')
 fireEvent.click(screen.getByRole('button',{name:'重新检测'}))
 await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2))
})
