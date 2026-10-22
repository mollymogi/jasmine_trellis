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
 fireEvent.click(screen.getByRole('button',{name:'启用采集'}));await waitFor(()=>expect(changed).toHaveBeenCalledOnce())
 expect(fetcher.mock.calls[0]?.[0]).toBe('/_local/agents/grok/capture');expect(fetcher.mock.calls[0]?.[1].body).toBe('{"action":"enable"}')
 expect(screen.getByText('已安装配置，请新开 Grok 会话发送普通消息。')).toBeTruthy()
 rerender(<SessionProvider><AgentCaptureAction agent={{...agent,capture:{...agent.capture!,configured:true}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('已启用 · 等待首条消息')).toBeTruthy();expect(screen.getByRole('button',{name:'停用采集'})).toBeTruthy()
 rerender(<SessionProvider><AgentCaptureAction agent={{...agent,capture:{...agent.capture!,configured:true,last_event_id:'evt_real'}}} onChanged={changed} refreshing={false}/></SessionProvider>)
 expect(screen.getByText('已启用 · 已收到消息')).toBeTruthy()
})
