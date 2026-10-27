import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import { AgentCaptureAction } from '../src/components/AgentCaptureAction'
import { SessionProvider } from '../src/app/session'
import type { DetectedAgent } from '../src/app/useAgentDiscovery'
afterEach(()=>vi.unstubAllGlobals())
const agent:DetectedAgent={id:'grok',name:'Grok',cli:true,desktop:false,detected:true,capture_supported:true,capture:{configured:false,can_manage:true,available:true,configuration_changed:false}}
it('enables a fixed Grok action and keeps installed configuration distinct from received messages',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true});vi.stubGlobal('fetch',fetcher);const changed=vi.fn()
 const {rerender}=render(<SessionProvider><AgentCaptureAction agent={agent} onChanged={changed} refreshing={false}/></SessionProvider>)
 fireEvent.click(screen.getByRole('button',{name:'一键配置 Hook'}));await waitFor(()=>expect(changed).toHaveBeenCalledOnce())
 expect(fetcher.mock.calls[0]?.[0]).toBe('/_local/agents/grok/capture');expect(fetcher.mock.calls[0]?.[1].body).toBe('{"action":"enable"}')
 expect(screen.getByText('已安装配置，请新开 Grok 会话发送普通消息。')).toBeTruthy()
 rerender(<SessionProvider><AgentCaptureAction agent={{...agent,capture:{...agent.capture!,configured:true}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('已启用 · 等待首条消息')).toBeTruthy();expect(screen.getByRole('button',{name:'停用 Hook'})).toBeTruthy();expect(screen.getByRole('button',{name:'重新配置 Hook'})).toBeTruthy()
 rerender(<SessionProvider><AgentCaptureAction agent={{...agent,capture:{...agent.capture!,configured:true,last_event_id:'evt_real'}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('已启用 · 已收到消息')).toBeTruthy()
})

it.each(['claude','codex'])('shows an actual per-agent configure action for %s',async id=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true});vi.stubGlobal('fetch',fetcher)
 render(<SessionProvider><AgentCaptureAction agent={{...agent,id,name:id==='codex'?'Codex':'Claude'}} onChanged={()=>{}} refreshing={false}/></SessionProvider>)
 fireEvent.click(screen.getByRole('button',{name:'一键配置 Hook'}))
 await waitFor(()=>expect(fetcher).toHaveBeenCalledOnce());expect(fetcher.mock.calls[0]?.[0]).toBe(`/_local/agents/${id}/capture`)
})
it('makes unsupported and client-disabled states explicit rather than offering an ineffective installer',()=>{
 const {rerender}=render(<SessionProvider><AgentCaptureAction agent={{...agent,id:'opencode',capture_supported:false}} onChanged={()=>{}} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('暂不支持自动接入')).toBeTruthy();expect(screen.queryByRole('button')).toBeNull()
 rerender(<SessionProvider><AgentCaptureAction agent={{...agent,id:'claude',capture:{...agent.capture!,client_hooks_disabled:true}}} onChanged={()=>{}} refreshing={false}/></SessionProvider>)
 expect((screen.getByRole('button',{name:'一键配置 Hook'}) as HTMLButtonElement).disabled).toBe(true)
})
it('offers the Memory hint only once capture is configured and posts to the fixed hint route',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true});vi.stubGlobal('fetch',fetcher);const changed=vi.fn()
 const codex={...agent,id:'codex',name:'Codex'}
 const {rerender}=render(<SessionProvider><AgentCaptureAction agent={{...codex,capture:{...agent.capture!,memory_hint:{available:true,enabled:false}}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('Memory 提示：未开启')).toBeTruthy()
 expect(screen.queryByRole('button',{name:'开启 Memory 提示'})).toBeNull()
 expect(screen.getByText('先启用对话采集，才能开启 Memory 提示。')).toBeTruthy()
 rerender(<SessionProvider><AgentCaptureAction agent={{...codex,capture:{...agent.capture!,configured:true,memory_hint:{available:true,enabled:false}}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 fireEvent.click(screen.getByRole('button',{name:'开启 Memory 提示'}))
 await waitFor(()=>expect(changed).toHaveBeenCalledOnce())
 expect(fetcher.mock.calls[0]?.[0]).toBe('/_local/agents/codex/memory-hint');expect(fetcher.mock.calls[0]?.[1].body).toBe('{"action":"enable"}')
 rerender(<SessionProvider><AgentCaptureAction agent={{...codex,capture:{...agent.capture!,configured:true,memory_hint:{available:true,enabled:true}}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('Memory 提示：已开启')).toBeTruthy()
 fireEvent.click(screen.getByRole('button',{name:'关闭 Memory 提示'}))
 await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));expect(fetcher.mock.calls[1]?.[1].body).toBe('{"action":"disable"}')
 rerender(<SessionProvider><AgentCaptureAction agent={{...codex,capture:{...agent.capture!,configured:true,memory_hint:{available:false,enabled:false}}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('Memory 提示：本机尚未准备提示凭证')).toBeTruthy()
 expect(screen.queryByRole('button',{name:/Memory 提示/})).toBeNull()
})
