/** Local operator control. Model IDs only; no commands, credentials or URLs from the browser. */
import http from 'node:http'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
const execute=promisify(execFile)
const checks=new Map()
import { constants, openSync, closeSync, fstatSync, readFileSync, writeFileSync, writeSync, renameSync, unlinkSync } from 'node:fs'
import { isAbsolute } from 'node:path'
import { createHash, timingSafeEqual, randomUUID } from 'node:crypto'

function privateBytes(path, limit = 16384) {
 const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK)
 try {const s=fstatSync(fd);if(!s.isFile()||s.uid!==process.getuid()||(s.mode&0o077)||s.size>limit)throw Error('unsafe_config');return readFileSync(fd)}finally{closeSync(fd)}
}
const privateJson=(path,limit)=>JSON.parse(privateBytes(path,limit).toString('utf8'))
const modelId=value=>typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,159}$/.test(value)
export function loadInterpreterProfile(path=process.env.JASMINE_INTERPRETER_CONTROL_CONFIG) {
 try {
  if(!path||!isAbsolute(path))return null
  const p=privateJson(path,65536)
  if(![1,2].includes(p.version)||p.enabled!==true||!['browser_token_sha256','catalog_sha256'].every(k=>/^[a-f0-9]{64}$/.test(p[k])))return null
  if(!['catalog','selection','audit'].every(k=>typeof p[k]==='string'&&isAbsolute(p[k])&&!p[k].includes('\0')))return null
  if(p.version===2&&(!Array.isArray(p.providers)||!['python','test_runner','profile_path'].every(k=>typeof p[k]==='string'&&isAbsolute(p[k]))))return null
  return p
 }catch{return null}
}
function allowedModels(profile) {
 const raw=privateBytes(profile.catalog,512*1024),doc=JSON.parse(raw.toString('utf8'))
 if(createHash('sha256').update(raw).digest('hex')!==profile.catalog_sha256)throw Error('catalog_changed')
 if(!Array.isArray(doc.models)||!doc.models.length)throw Error('invalid_catalog')
 const models=doc.models.map(m=>{
  if(!modelId(m.slug)||m.apply_patch_tool_type!==null||!Array.isArray(m.experimental_supported_tools)||m.experimental_supported_tools.length||m.supports_search_tool!==false||m.tool_mode!==null)throw Error('unsafe_model')
  return {id:m.slug,label:typeof m.display_name==='string'?m.display_name.slice(0,100):m.slug}
 })
 if(new Set(models.map(m=>m.id)).size!==models.length)throw Error('duplicate_model')
 return models
}
function providers(profile) {
 const codexModels=allowedModels(profile)
 if(profile.version===1)return [{id:'codex',label:'Codex',detected:true,enabled:true,models:codexModels}]
 const result=profile.providers.map(p=>{
  if(!['codex','claude','grok','opencode'].includes(p.id)||!Array.isArray(p.models)||!p.models.every(m=>modelId(m.id)&&typeof m.label==='string'))throw Error('invalid_provider')
  return {id:p.id,label:p.label,detected:p.detected===true,enabled:p.enabled===true,models:p.id==='codex'?codexModels:p.models,version:p.version}
 })
 if(new Set(result.map(p=>p.id)).size!==result.length)throw Error('duplicate_provider')
 return result
}
function selection(profile,choices) {
 const s=privateJson(profile.selection)
 const keys=s.version===1?'model,revision,version':'model,provider,revision,version'
 if(Object.keys(s).sort().join(',')!==keys||![1,2].includes(s.version)||!Number.isSafeInteger(s.revision)||s.revision<1)throw Error('invalid_selection')
 const provider=s.provider??'codex',p=choices.find(p=>p.id===provider)
 if(!p||!p.detected||!p.enabled||!p.models.some(m=>m.id===s.model))throw Error('invalid_selection')
 return {...s,provider}
}
function manages(profile,authorization) {
 if(!profile||!authorization?.startsWith('Bearer '))return false
 return timingSafeEqual(createHash('sha256').update(authorization.slice(7)).digest(),Buffer.from(profile.browser_token_sha256,'hex'))
}
export function readInterpreterState(profile,authorization) {
 const choices=providers(profile),s=selection(profile,choices),p=choices.find(p=>p.id===s.provider)
 return {provider:s.provider,model:s.model,revision:s.revision,models:p.models,...(profile.version===2?{providers:choices}:{}),can_manage:manages(profile,authorization),login:'native_cli_login',applies_to:'new_analyses',automatic_fallback:false}
}
export function selectInterpreterModel(profile,body,authorization) {
 if(!manages(profile,authorization))throw Object.assign(Error('management_not_granted'),{status:403})
 if(!body||!['expected_revision,model','expected_revision,model,provider'].includes(Object.keys(body).sort().join(','))||!modelId(body.model)||!Number.isSafeInteger(body.expected_revision))throw Object.assign(Error('invalid_selection'),{status:400})
 const choices=providers(profile),previous=selection(profile,choices),client=body.provider??'codex',picked=choices.find(p=>p.id===client)
 if(!picked?.detected||!picked.enabled||!picked.models.some(m=>m.id===body.model))throw Object.assign(Error('unsupported_model'),{status:400})
 if(previous.revision!==body.expected_revision)throw Object.assign(Error('selection_changed'),{status:409})
 if(previous.revision===Number.MAX_SAFE_INTEGER)throw Error('revision_exhausted')
 if(previous.model===body.model&&previous.provider===client)return readInterpreterState(profile,authorization)
 const audit=openSync(profile.audit,constants.O_WRONLY|constants.O_CREAT|constants.O_APPEND|constants.O_NOFOLLOW,0o600)
 const temp=profile.selection+'.tmp-'+randomUUID()
 try {
  const s=fstatSync(audit);if(!s.isFile()||s.uid!==process.getuid()||(s.mode&0o077))throw Error('unsafe_audit')
  // A revision check plus synchronous mutation prevents concurrent tab overwrites.
  writeFileSync(profile.selection+'.backup-'+previous.revision+'-'+randomUUID(),JSON.stringify(previous.version===1?{version:1,model:previous.model,revision:previous.revision}:previous)+'\n',{flag:'wx',mode:0o600})
  const next=profile.version===2?{version:2,provider:client,model:body.model,revision:previous.revision+1}:{version:1,model:body.model,revision:previous.revision+1}
  writeFileSync(temp,JSON.stringify(next)+'\n',{flag:'wx',mode:0o600})
  writeSync(audit,JSON.stringify({action:'SELECT_MODEL',from_provider:previous.provider,to_provider:client,from:previous.model,to:next.model,revision:next.revision,checked_at:new Date().toISOString()})+'\n')
  renameSync(temp,profile.selection)
 }finally{closeSync(audit);try{unlinkSync(temp)}catch{}}
 return readInterpreterState(profile,authorization)
}
function send(res,status,body){res.writeHead(status,{'content-type':'application/json','cache-control':'no-store','x-content-type-options':'nosniff'});res.end(JSON.stringify(body))}
export async function serveLocalInterpreter(req,res,origin,profile=loadInterpreterProfile()) {
 let host;try{host=new URL('http://'+req.headers.host).hostname}catch{}
 if(!['127.0.0.1','localhost','[::1]'].includes(host)||req.headers['sec-fetch-site']==='cross-site'||(req.headers.origin&&req.headers.origin!=='http://'+req.headers.host)){send(res,403,{error:'local_origin_required'});return}
 if(!['GET','POST'].includes(req.method)){send(res,405,{error:'method_not_allowed'});return}
 if(req.method==='POST'&&req.headers.origin!=='http://'+req.headers.host){send(res,403,{error:'same_origin_required'});return}
 const authorization=req.headers.authorization
 if(!authorization?.startsWith('Bearer ')){send(res,401,{error:'authentication_required'});return}
 const status=await new Promise(resolve=>{
  const check=http.get(new URL('/v1/projects?limit=1',origin),{headers:{authorization}},r=>{r.resume();resolve(r.statusCode)})
  check.setTimeout(3000,()=>check.destroy());check.on('error',()=>resolve(502))
 })
 if(status!==200){send(res,[401,403].includes(status)?status:502,{error:'core_authorization_failed'});return}
 if(!profile){send(res,503,{error:'interpreter_control_unconfigured'});return}
 try {
  if(req.method==='GET'){send(res,200,readInterpreterState(profile,authorization));return}
  let body='';for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>1024){send(res,413,{error:'request_too_large'});return}}
  let value;try{value=JSON.parse(body)}catch{send(res,400,{error:'invalid_json'});return}
  if(value?.action==='test'){
   if(!manages(profile,authorization)){send(res,403,{error:'management_not_granted'});return}
   if(profile.version!==2||Object.keys(value).sort().join(',')!=='action,model,provider'||!providers(profile).some(p=>p.id===value.provider&&p.detected&&p.enabled&&p.models.some(m=>m.id===value.model))){send(res,400,{error:'invalid_test'});return}
   const key=profile.profile_path+':'+value.provider+':'+value.model
   if(checks.size&&!checks.has(key)){send(res,409,{error:'check_in_progress'});return}
   if(!checks.has(key))checks.set(key,execute(profile.python,['-I','-B',profile.test_runner,'--profile',profile.profile_path,'--provider',value.provider,'--model',value.model],{timeout:150000,maxBuffer:8192,env:Object.fromEntries(Object.entries(process.env).filter(([k])=>['HOME','PATH','TMPDIR','LANG','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy'].includes(k)))}).then(r=>JSON.parse(r.stdout)).finally(()=>checks.delete(key)))
   send(res,200,await checks.get(key));return
  }
  send(res,200,selectInterpreterModel(profile,value,authorization))
 }catch(error){send(res,error.status??503,{error:error.status?error.message:'interpreter_control_unavailable'})}
}
