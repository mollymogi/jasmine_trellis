// @vitest-environment node
import http from 'node:http'
import { once } from 'node:events'
import { mkdtempSync,writeFileSync,readFileSync,chmodSync,symlinkSync,rmSync,readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
import { afterEach,expect,it } from 'vitest'
import { loadInterpreterProfile,readInterpreterState,selectInterpreterModel,serveLocalInterpreter } from '../server/interpreter-control.mjs'
const roots:string[]=[],servers:http.Server[]=[]
afterEach(async()=>{for(const s of servers.splice(0)){s.closeAllConnections();await new Promise<void>(r=>s.close(()=>r()))}for(const r of roots.splice(0))rmSync(r,{recursive:true,force:true})})
async function listen(handler:http.RequestListener){const s=http.createServer(handler);servers.push(s);s.listen(0,'127.0.0.1');await once(s,'listening');return new URL('http://127.0.0.1:'+(s.address() as any).port)}
function fixture(){
 const root=mkdtempSync(join(tmpdir(),'jasmine-model-'));roots.push(root);const catalog=join(root,'catalog.json'),selection=join(root,'choice.json'),audit=join(root,'audit.jsonl'),file=join(root,'profile.json')
 const raw=JSON.stringify({models:['gpt-6.1-sol','gpt-6-luna'].map(slug=>({slug,display_name:slug,apply_patch_tool_type:null,experimental_supported_tools:[],supports_search_tool:false,tool_mode:null}))})
 writeFileSync(catalog,raw,{mode:0o600});writeFileSync(selection,JSON.stringify({version:1,model:'gpt-6.1-sol',revision:1}),{mode:0o600})
 const profile={version:1,enabled:true,catalog,selection,audit,catalog_sha256:createHash('sha256').update(raw).digest('hex'),browser_token_sha256:createHash('sha256').update('manager').digest('hex')};writeFileSync(file,JSON.stringify(profile),{mode:0o600});return {root,file,profile}
}
it('pins safe models, preserves CAS history and excludes secrets/paths from readback',()=>{
 const {root,file,profile}=fixture();expect(loadInterpreterProfile(file)).not.toBeNull()
 const first=readInterpreterState(profile,'Bearer reader');expect(first).toMatchObject({model:'gpt-6.1-sol',can_manage:false,revision:1});expect(JSON.stringify(first)).not.toContain(root)
 const next=selectInterpreterModel(profile,{model:'gpt-6-luna',expected_revision:1},'Bearer manager');expect(next).toMatchObject({model:'gpt-6-luna',revision:2,automatic_fallback:false})
 expect(()=>selectInterpreterModel(profile,{model:'gpt-6.1-sol',expected_revision:1},'Bearer manager')).toThrow('selection_changed')
 expect(JSON.parse(readFileSync(profile.audit,'utf8'))).toMatchObject({from:'gpt-6.1-sol',to:'gpt-6-luna',revision:2})
 expect(()=>selectInterpreterModel(profile,{model:'gpt-unknown',expected_revision:2},'Bearer manager')).toThrow('unsupported_model')
 expect(()=>selectInterpreterModel(profile,{model:'gpt-6.1-sol',expected_revision:2,command:'arbitrary'},'Bearer manager')).toThrow('invalid_selection')
 writeFileSync(profile.catalog,'{}');expect(()=>readInterpreterState(profile,'Bearer manager')).toThrow('catalog_changed')
 chmodSync(file,0o644);expect(loadInterpreterProfile(file)).toBeNull()
})
it('requires loopback same-origin, live Core auth and exact operator grant for model changes',async()=>{
 const {profile}=fixture(),core=await listen((req,res)=>{res.writeHead(req.headers.authorization==='Bearer revoked'?403:200);res.end('{}')}),host=await listen((req,res)=>{void serveLocalInterpreter(req,res,core,profile)}),url=new URL('/_local/interpreter',host)
 const post=(token:string,origin=host.origin)=>fetch(url,{method:'POST',headers:{Authorization:'Bearer '+token,Origin:origin},body:JSON.stringify({model:'gpt-6-luna',expected_revision:1})})
 expect((await fetch(url)).status).toBe(401);expect((await post('revoked')).status).toBe(403);expect((await post('reader')).status).toBe(403);expect((await post('manager','http://evil.invalid')).status).toBe(403)
 expect(readInterpreterState(profile,'Bearer manager').revision).toBe(1)
 expect((await post('manager')).status).toBe(200);expect((await post('manager')).status).toBe(409)
 const read=await fetch(url,{headers:{Authorization:'Bearer reader'}}).then(r=>r.json());expect(read).toMatchObject({model:'gpt-6-luna',can_manage:false,revision:2})
})
it('rejects symlinked selection instead of exposing or overwriting another file',()=>{
 const {root,profile}=fixture(),target=join(root,'target.json');writeFileSync(target,JSON.stringify({version:1,model:'gpt-6.1-sol',revision:1}),{mode:0o600});rmSync(profile.selection);symlinkSync(target,profile.selection)
 expect(()=>selectInterpreterModel(profile,{model:'gpt-6-luna',expected_revision:1},'Bearer manager')).toThrow()
 expect(JSON.parse(readFileSync(target,'utf8')).model).toBe('gpt-6.1-sol')
})
it('selects only enabled provider/model pairs and restores a valid v1 backup',()=>{
 const {root,profile}=fixture();Object.assign(profile,{version:2,providers:[{id:'codex',label:'Codex',detected:true,enabled:true,models:[]},{id:'grok',label:'Grok',detected:true,enabled:true,models:[{id:'grok-4.6',label:'Grok'}]},{id:'opencode',label:'OpenCode',detected:true,enabled:false,models:[{id:'other/model',label:'Deferred'}]}]})
 expect(()=>selectInterpreterModel(profile,{provider:'opencode',model:'other/model',expected_revision:1},'Bearer manager')).toThrow('unsupported_model')
 expect(()=>selectInterpreterModel(profile,{provider:'grok',model:'gpt-6-luna',expected_revision:1},'Bearer manager')).toThrow('unsupported_model')
 const result=selectInterpreterModel(profile,{provider:'grok',model:'grok-4.6',expected_revision:1},'Bearer manager');expect(result).toMatchObject({provider:'grok',model:'grok-4.6',revision:2})
 expect(JSON.parse(readFileSync(profile.selection,'utf8'))).toEqual({version:2,provider:'grok',model:'grok-4.6',revision:2})
 expect(JSON.stringify(result)).not.toContain(root)
 const backup=readdirSync(root).find(f=>f.includes('.backup-1-'))!;expect(JSON.parse(readFileSync(join(root,backup),'utf8'))).toEqual({version:1,model:'gpt-6.1-sol',revision:1})
})
it('runs a fixed health helper only for granted, enabled catalog choices',async()=>{
 const {root,profile}=fixture(),helper=join(root,'fixed-check');writeFileSync(helper,'#!/bin/sh\nprintf \'{"status":"passed"}\\n\'\n',{mode:0o700})
 Object.assign(profile,{version:2,python:helper,test_runner:join(root,'operator-check.py'),profile_path:join(root,'profile.json'),providers:[{id:'codex',label:'Codex',detected:true,enabled:true,models:[]},{id:'grok',label:'Grok',detected:true,enabled:true,models:[{id:'grok-4.6',label:'Grok'}]},{id:'opencode',label:'OpenCode',detected:true,enabled:false,models:[]}]})
 const core=await listen((_,res)=>{res.writeHead(200);res.end('{}')}),host=await listen((req,res)=>{void serveLocalInterpreter(req,res,core,profile)}),url=new URL('/_local/interpreter',host)
 const post=(token:string,body:any)=>fetch(url,{method:'POST',headers:{Authorization:'Bearer '+token,Origin:host.origin},body:JSON.stringify(body)})
 const body={action:'test',provider:'grok',model:'grok-4.6'}
 expect((await post('reader',body)).status).toBe(403);expect((await post('manager',{...body,command:'echo unwanted'})).status).toBe(400);expect((await post('manager',{...body,provider:'opencode'})).status).toBe(400)
 expect(await (await post('manager',body)).json()).toEqual({status:'passed'});expect(readInterpreterState(profile,'Bearer manager')).toMatchObject({provider:'codex',revision:1})
})
