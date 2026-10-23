import { join } from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { createDashboardServer, isAllowed, loadAllowlist } from '../server/serve.mjs'
const previous=process.env.P6_ENABLE_CONVERSATION_PROCESSING
const event='evt_01M5HK69J9GTS6M1G8AXBWTA9S'
afterEach(()=>{if(previous===undefined)delete process.env.P6_ENABLE_CONVERSATION_PROCESSING;else process.env.P6_ENABLE_CONVERSATION_PROCESSING=previous;vi.resetModules()})
it.each([undefined,'0','1',' yes '])('gates source-only processing routes consistently: %j',async value=>{
 if(value===undefined)delete process.env.P6_ENABLE_CONVERSATION_PROCESSING;else process.env.P6_ENABLE_CONVERSATION_PROCESSING=value
 vi.resetModules();const dev=await import('../server/core-proxy'),enabled=value==='1'||value===' yes '
 const list=loadAllowlist(join(process.cwd(),'server'),{enableConversationProcessing:enabled})
 for(const [method,path,allowed] of [['GET','/v1/conversation-processing',enabled],['POST','/v1/conversation-processing/enqueue',enabled],['POST',`/v1/conversation-processing/${event}/retry`,enabled],['GET','/v1/conversation-processing/enqueue',false],['POST','/v1/conversation-processing',false],['DELETE',`/v1/conversation-processing/${event}`,false],['POST','/v1/auth/keys',false],['GET','/v1/audit',false]] as const){expect(dev.isAllowedCoreRequest(method,path)).toBe(allowed);expect(isAllowed(list,method,path)).toBe(allowed)}
})
it('rejects a foreign-origin enqueue before Core',async()=>{
 const server=createDashboardServer({root:'/unused',origin:new URL('http://127.0.0.1:1'),allowlist:loadAllowlist(join(process.cwd(),'server'),{enableConversationProcessing:true})})
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve))
 try{const addr=server.address();if(!addr||typeof addr==='string')throw Error('missing address');const r=await fetch(`http://127.0.0.1:${addr.port}/v1/conversation-processing/enqueue`,{method:'POST',headers:{origin:'http://unrelated.invalid','content-type':'application/json'},body:'{}'});expect(r.status).toBe(403);expect((await r.json()).error.code).toBe('proxy_origin_not_allowed')}
 finally{await new Promise<void>(resolve=>server.close(()=>resolve()))}
})
it('routes model settings to authenticated local control without crashing the dashboard',async()=>{
 process.env.P6_ENABLE_CONVERSATION_PROCESSING='1'
 const server=createDashboardServer({root:'/unused',origin:new URL('http://127.0.0.1:1'),allowlist:loadAllowlist(join(process.cwd(),'server'),{enableConversationProcessing:true})})
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve))
 try{const addr=server.address();if(!addr||typeof addr==='string')throw Error('missing address');const r=await fetch(`http://127.0.0.1:${addr.port}/_local/interpreter`);expect(r.status).toBe(401);expect((await r.json()).error).toBe('authentication_required')}
 finally{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()))}
})
