// @vitest-environment node
import http from 'node:http'
import { once } from 'node:events'
import { mkdtempSync,writeFileSync,readFileSync,chmodSync,symlinkSync,existsSync,rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
import { afterEach,expect,it } from 'vitest'
import { serveLocalAgents,discoverAgents } from '../server/local-agents.mjs'
import { loadCaptureProfile,captureStates,controlCapture,type CaptureProfile } from '../server/agent-capture-control.mjs'
const dirs:string[]=[],servers:http.Server[]=[]
afterEach(async()=>{await Promise.all(servers.splice(0).map(server=>new Promise<void>(resolve=>{server.closeAllConnections();server.close(()=>resolve())})));dirs.splice(0).forEach(path=>rmSync(path,{recursive:true,force:true}))})
function fixture(){
 const root=mkdtempSync(join(tmpdir(),'jasmine-grok-control-'));dirs.push(root)
 const helper=join(root,'installer.py');writeFileSync(helper,`import pathlib,json,time\ntime.sleep(.05)\np=pathlib.Path(${JSON.stringify(join(root,'called'))})\np.write_text(p.read_text()+'x' if p.exists() else 'x')\nprint(json.dumps({'status':'installed'}))\n`)
 const profile:CaptureProfile={version:1,enabled:true,browser_token_sha256:createHash('sha256').update('manager').digest('hex'),python:'/usr/bin/python3',installer:helper,launcher:helper,device_binding:join(root,'device.json'),grok_home:root,receipts:join(root,'native.jsonl'),control_receipts:join(root,'control.jsonl')}
 const file=join(root,'profile.json');writeFileSync(file,JSON.stringify(profile),{mode:0o600});return {root,file,profile}
}
async function listen(handler:http.RequestListener){const server=http.createServer(handler);servers.push(server);server.listen(0,'127.0.0.1');await once(server,'listening');return new URL(`http://127.0.0.1:${(server.address() as {port:number}).port}`)}
it('requires an operator-owned private profile and rejects a symlink or broad permissions',()=>{
 const {file}=fixture();expect(loadCaptureProfile(file)).not.toBeNull();chmodSync(file,0o644);expect(loadCaptureProfile(file)).toBeNull();chmodSync(file,0o600)
 symlinkSync(file,file+'.link');expect(loadCaptureProfile(file+'.link')).toBeNull()
})
it('never lets a reader key, foreign origin, body-selected command, or revoked key install capture',async()=>{
 const {root,profile}=fixture()
 const core=await listen((req,res)=>{res.writeHead(req.headers.authorization==='Bearer revoked'?403:200);res.end('{}')})
 const host=await listen((req,res)=>{void serveLocalAgents(req,res,core,discoverAgents,profile)})
 const url=new URL('/_local/agents/grok/capture',host)
 const post=(token:string,body:unknown,origin=host.origin)=>fetch(url,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',Origin:origin},body:JSON.stringify(body)})
 expect((await post('reader',{action:'enable'})).status).toBe(403)
 expect((await post('manager',{action:'enable'},'http://evil.invalid')).status).toBe(403)
 expect((await post('manager',{action:'enable',command:'arbitrary'})).status).toBe(400)
 expect((await post('revoked',{action:'enable'})).status).toBe(403)
 expect(existsSync(join(root,'called'))).toBe(false)
 const reply=await post('manager',{action:'enable'});expect(reply.status).toBe(200);expect((await reply.json()).status).toBe('installed')
 expect(readFileSync(join(root,'called'),'utf8')).toBe('x')
 const inventory=await fetch(new URL('/_local/agents',host),{headers:{Authorization:'Bearer manager'}}).then(response=>response.text())
 expect(inventory).not.toContain(root);expect(inventory).not.toContain(profile.browser_token_sha256)
 expect(JSON.parse(readFileSync(profile.control_receipts,'utf8')).action).toBe('enable')
})
it('coalesces simultaneous enable clicks and distinguishes configuration from native message evidence',async()=>{
 const {root,profile}=fixture();await Promise.all([controlCapture(profile,'enable'),controlCapture(profile,'enable')]);expect(readFileSync(join(root,'called'),'utf8')).toBe('x')
 expect(captureStates(profile,'Bearer manager').grok).toMatchObject({configured:false,can_manage:true})
 writeFileSync(profile.receipts,JSON.stringify({status:'accepted',event_type:'assistant.message',event_id:'evt_native',checked_at:'2026-10-22T10:00:00Z'})+'\n',{mode:0o600})
 expect(captureStates(profile,'Bearer reader').grok).toMatchObject({configured:false,can_manage:false,last_event_id:'evt_native'})
})

it('routes each v2 client to its pinned installer and validates every adapter path',async()=>{
 const {root,file,profile}=fixture()
 const adapters=Object.fromEntries(['claude','codex','grok'].map(agent=>[agent,{...profile,home:join(root,agent)}]))
 const multi={...profile,version:2,adapters};writeFileSync(file,JSON.stringify(multi));expect(loadCaptureProfile(file)).not.toBeNull()
 const core=await listen((_req,res)=>{res.writeHead(200);res.end('{}')})
 const host=await listen((req,res)=>{void serveLocalAgents(req,res,core,discoverAgents,multi)})
 for(const agent of ['claude','codex','grok']){
  const reply=await fetch(new URL(`/_local/agents/${agent}/capture`,host),{method:'POST',headers:{Authorization:'Bearer manager',Origin:host.origin},body:JSON.stringify({action:'enable'})})
  expect(reply.status).toBe(200)
 }
 expect(readFileSync(join(root,'called'),'utf8')).toBe('xxx')
 expect(captureStates(multi,'Bearer manager').claude).toMatchObject({can_manage:true,available:true,configured:false})
 multi.adapters.claude!.installer='relative-path';writeFileSync(file,JSON.stringify(multi));expect(loadCaptureProfile(file)).toBeNull()
})

it('toggles the Memory hint only through the pinned installer and reads it back from the device binding',async()=>{
 const {root,profile}=fixture()
 const helper=join(root,'hint-installer.py')
 writeFileSync(helper,`import sys,json,pathlib\npathlib.Path(${JSON.stringify(join(root,'argv.json'))}).write_text(json.dumps(sys.argv[1:]))\nprint(json.dumps({'status':'memory_hint_enabled' if 'enable' in sys.argv else 'memory_hint_disabled'}))\n`)
 const adapters=Object.fromEntries(['claude','codex','grok'].map(agent=>[agent,{...profile,installer:helper,home:join(root,agent)}]))
 const multi={...profile,version:2,adapters}
 const core=await listen((req,res)=>{res.writeHead(req.headers.authorization==='Bearer manager'||req.headers.authorization==='Bearer reader'?200:403);res.end('{}')})
 const host=await listen((req,res)=>{void serveLocalAgents(req,res,core,discoverAgents,multi)})
 const post=(agent:string,token:string,body:unknown)=>fetch(new URL(`/_local/agents/${agent}/memory-hint`,host),{method:'POST',headers:{Authorization:'Bearer '+token,Origin:host.origin},body:JSON.stringify(body)})
 expect((await post('grok','manager',{action:'enable'})).status).toBe(404)
 expect((await post('codex','reader',{action:'enable'})).status).toBe(403)
 expect((await post('codex','manager',{action:'enable',key:'x'})).status).toBe(400)
 expect(existsSync(join(root,'argv.json'))).toBe(false)
 const reply=await post('codex','manager',{action:'enable'});expect(reply.status).toBe(200);expect((await reply.json()).status).toBe('memory_hint_enabled')
 const argv=JSON.parse(readFileSync(join(root,'argv.json'),'utf8')) as string[]
 expect(argv.slice(-2)).toEqual(['--memory-hint','enable']);expect(argv).not.toContain('--disable')
 expect(readFileSync(profile.control_receipts,'utf8')).toContain('"action":"memory-hint:enable"')
 // Readback: unavailable without a hint key, and never "enabled" while capture is not configured.
 expect(captureStates(multi,'Bearer manager').codex?.memory_hint).toEqual({available:false,enabled:false})
 expect(captureStates(multi,'Bearer manager').grok?.memory_hint).toBeUndefined()
 writeFileSync(profile.device_binding,JSON.stringify({memory_key_file:join(root,'memory.key'),memory_hint:true}),{mode:0o600})
 expect(captureStates(multi,'Bearer manager').codex?.memory_hint).toEqual({available:true,enabled:false})
 const command=['/usr/bin/python3','-I','-B','-m','jasmine_core.capture.codex_session','--device-binding',profile.device_binding,'--receipts',profile.receipts].join(' ')
 const {mkdirSync}=await import('node:fs');mkdirSync(join(root,'codex'))
 writeFileSync(join(root,'codex','hooks.json'),JSON.stringify({hooks:Object.fromEntries(['SessionStart','UserPromptSubmit','Stop','SessionEnd'].map(event=>[event,[{hooks:[{type:'command',command,statusMessage:'Jasmine daily conversation capture'}]}]]))}),{mode:0o600})
 expect(captureStates(multi,'Bearer manager').codex).toMatchObject({configured:true,memory_hint:{available:true,enabled:true}})
 chmodSync(profile.device_binding,0o644)
 expect(captureStates(multi,'Bearer manager').codex?.memory_hint).toEqual({available:false,enabled:false})
})
