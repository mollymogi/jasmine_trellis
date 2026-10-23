/** Local operator control. Model IDs only; no commands, credentials or URLs from the browser. */
import http from 'node:http'
import { constants, openSync, closeSync, fstatSync, readFileSync, writeFileSync, writeSync, renameSync, unlinkSync } from 'node:fs'
import { isAbsolute } from 'node:path'
import { createHash, timingSafeEqual, randomUUID } from 'node:crypto'

function privateBytes(path, limit = 16384) {
 const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK)
 try {const s=fstatSync(fd);if(!s.isFile()||s.uid!==process.getuid()||(s.mode&0o077)||s.size>limit)throw Error('unsafe_config');return readFileSync(fd)}finally{closeSync(fd)}
}
const privateJson=(path,limit)=>JSON.parse(privateBytes(path,limit).toString('utf8'))
const modelId=value=>typeof value==='string'&&/^[a-z0-9][a-z0-9.-]{0,79}$/.test(value)
export function loadInterpreterProfile(path=process.env.JASMINE_INTERPRETER_CONTROL_CONFIG) {
 try {
  if(!path||!isAbsolute(path))return null
  const p=privateJson(path)
  if(p.version!==1||p.enabled!==true||!['browser_token_sha256','catalog_sha256'].every(k=>/^[a-f0-9]{64}$/.test(p[k])))return null
  if(!['catalog','selection','audit'].every(k=>typeof p[k]==='string'&&isAbsolute(p[k])&&!p[k].includes('\0')))return null
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
function selection(profile,models) {
 const s=privateJson(profile.selection)
 if(Object.keys(s).sort().join(',')!=='model,revision,version'||s.version!==1||!Number.isSafeInteger(s.revision)||s.revision<1||!models.some(m=>m.id===s.model))throw Error('invalid_selection')
 return s
}
function manages(profile,authorization) {
 if(!profile||!authorization?.startsWith('Bearer '))return false
 return timingSafeEqual(createHash('sha256').update(authorization.slice(7)).digest(),Buffer.from(profile.browser_token_sha256,'hex'))
}
export function readInterpreterState(profile,authorization) {
 const models=allowedModels(profile),s=selection(profile,models)
 return {model:s.model,revision:s.revision,models,can_manage:manages(profile,authorization),login:'codex_saved_login',applies_to:'new_analyses',automatic_fallback:false}
}
export function selectInterpreterModel(profile,body,authorization) {
 if(!manages(profile,authorization))throw Object.assign(Error('management_not_granted'),{status:403})
 if(!body||Object.keys(body).sort().join(',')!=='expected_revision,model'||!modelId(body.model)||!Number.isSafeInteger(body.expected_revision))throw Object.assign(Error('invalid_selection'),{status:400})
 const models=allowedModels(profile),previous=selection(profile,models)
 if(!models.some(m=>m.id===body.model))throw Object.assign(Error('unsupported_model'),{status:400})
 if(previous.revision!==body.expected_revision)throw Object.assign(Error('selection_changed'),{status:409})
 if(previous.revision===Number.MAX_SAFE_INTEGER)throw Error('revision_exhausted')
 if(previous.model===body.model)return readInterpreterState(profile,authorization)
 const audit=openSync(profile.audit,constants.O_WRONLY|constants.O_CREAT|constants.O_APPEND|constants.O_NOFOLLOW,0o600)
 const temp=profile.selection+'.tmp-'+randomUUID()
 try {
  const s=fstatSync(audit);if(!s.isFile()||s.uid!==process.getuid()||(s.mode&0o077))throw Error('unsafe_audit')
  // A revision check plus synchronous mutation prevents concurrent tab overwrites.
  writeFileSync(profile.selection+'.backup-'+previous.revision+'-'+randomUUID(),JSON.stringify(previous)+'\n',{flag:'wx',mode:0o600})
  const next={version:1,model:body.model,revision:previous.revision+1}
  writeFileSync(temp,JSON.stringify(next)+'\n',{flag:'wx',mode:0o600})
  writeSync(audit,JSON.stringify({action:'SELECT_MODEL',from:previous.model,to:next.model,revision:next.revision,checked_at:new Date().toISOString()})+'\n')
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
  send(res,200,selectInterpreterModel(profile,value,authorization))
 }catch(error){send(res,error.status??503,{error:error.status?error.message:'interpreter_control_unavailable'})}
}
