/** Operator-pinned installation only. Browser input cannot choose code, paths or keys. */
import { constants, openSync, closeSync, fstatSync, readFileSync, existsSync, writeSync, readSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { createHash, timingSafeEqual } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
const execute = promisify(execFile)
const LABELS={grok:'Jasmine daily Grok conversation capture',claude:'Jasmine daily Claude conversation capture',codex:'Jasmine daily conversation capture'}
export const CAPTURE_AGENTS=Object.keys(LABELS)
const EVENTS=['SessionStart','UserPromptSubmit','Stop','SessionEnd']
const inflight=new Map()
const quoted=value=>/^[\w@%+=:,./-]+$/.test(value)?value:"'"+value.replaceAll("'", "'\"'\"'")+"'"
export function loadCaptureProfile(path=process.env.JASMINE_AGENT_CAPTURE_CONTROL_CONFIG) {
 if(!path||!isAbsolute(path))return null
 let fd
 try {
  fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK)
  const stat=fstatSync(fd);if(!stat.isFile()||stat.uid!==process.getuid()||(stat.mode&0o077)||stat.size>16384)return null
  const profile=JSON.parse(readFileSync(fd,'utf8'))
  if(![1,2].includes(profile.version)||profile.enabled!==true||!/^[a-f0-9]{64}$/.test(profile.browser_token_sha256))return null
  const absolute=value=>typeof value==='string'&&isAbsolute(value)&&!value.includes('\0')
  if(!absolute(profile.control_receipts))return null
  if(profile.version===1){
   for(const key of ['python','installer','launcher','device_binding','receipts','grok_home'])if(!absolute(profile[key]))return null
  }else{
   if(!profile.adapters||typeof profile.adapters!=='object'||Array.isArray(profile.adapters))return null
   for(const [agent,adapter] of Object.entries(profile.adapters)){
    if(!CAPTURE_AGENTS.includes(agent)||!adapter||typeof adapter!=='object')return null
    for(const key of ['python','installer','device_binding','receipts','home'])if(!absolute(adapter[key]))return null
    if(agent!=='codex'&&!absolute(adapter.launcher))return null
   }
  }
  return profile
 }catch{return null}finally{if(fd!==undefined)closeSync(fd)}
}
export function canManageCapture(profile,token) {
 if(!profile||!token?.startsWith('Bearer '))return false
 const digest=createHash('sha256').update(token.slice(7)).digest()
 const expected=Buffer.from(profile.browser_token_sha256,'hex');return expected.length===digest.length&&timingSafeEqual(digest,expected)
}
function adapterFor(profile,agent) {
 if(!profile)return null
 if(profile.version===2)return profile.adapters?.[agent]??null
 if(agent==='grok')return {...profile,home:profile.grok_home}
 return null
}
function expectedCommand(adapter,agent){
 const prefix=agent==='codex'?[adapter.python,'-I','-B','-m','jasmine_core.capture.codex_session']:[adapter.python,'-I','-B',adapter.launcher]
 return [...prefix,'--device-binding',adapter.device_binding,'--receipts',adapter.receipts,...(agent==='codex'?[]:['--'+agent+'-home',adapter.home])].map(quoted).join(' ')
}
function readHookDocument(file) {
 let fd
 try {fd=openSync(file,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);const stat=fstatSync(fd)
  if(!stat.isFile()||stat.uid!==process.getuid()||stat.mode&0o022||stat.size>65536)throw Error('unsafe_hook_file')
  return JSON.parse(readFileSync(fd,'utf8'))
 }finally{if(fd!==undefined)closeSync(fd)}
}
function messageReceipt(file){
 if(!file)return {}
 let fd
 try {
  fd=openSync(file,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);const stat=fstatSync(fd)
  if(!stat.isFile()||stat.uid!==process.getuid()||stat.mode&0o077)return {}
  // Read a bounded tail; do not expose message content, paths or credentials.
  const size=Math.min(stat.size,131072), buffer=Buffer.alloc(size)
  // readFileSync would read the entire receipt history; a bounded positional read is intentional.
  readSync(fd,buffer,0,size,stat.size-size)
  const lines=buffer.toString('utf8').split('\n').slice(stat.size>size?1:0).filter(Boolean)
  const rows=lines.map(line=>{try{return JSON.parse(line)}catch{return null}}).filter(Boolean)
  const last=rows.findLast(row=>['accepted','failed'].includes(row.status)),message=rows.findLast(row=>row.status==='accepted'&&['user.prompt','assistant.message'].includes(row.event_type))
  return {last_message_at:message?.checked_at??null,last_event_id:message?.event_id??null,last_error:last?.status==='failed'?'native_capture_failed':null}
 }catch{return {}}finally{if(fd!==undefined)closeSync(fd)}
}
export function captureStates(profile,authorization) {
 const states={}
 for(const agent of CAPTURE_AGENTS){
  const adapter=adapterFor(profile,agent)
  let configured=false,drift=false,blocked=false
  if(adapter){
   const file=join(adapter.home,agent==='grok'?'hooks/jasmine-conversation.json':agent==='claude'?'settings.json':'hooks.json')
   try{
    const doc=readHookDocument(file),command=expectedCommand(adapter,agent)
    const owned=event=>doc.hooks?.[event]?.flatMap(group=>group.hooks??[]).filter(hook=>agent==='grok'||hook.statusMessage===LABELS[agent])??[]
    configured=(agent!=='grok'||doc.description===LABELS.grok)&&EVENTS.every(event=>{const handlers=owned(event);return handlers.length===1&&handlers[0].type==='command'&&handlers[0].command===command})
    drift=!configured&&(agent==='grok'||EVENTS.some(event=>owned(event).length>0))
    blocked=agent==='claude'&&doc.disableAllHooks===true
   }catch{drift=existsSync(file)&&agent==='grok'}
  }
  states[agent]={configured,can_manage:!!adapter&&canManageCapture(profile,authorization),available:!!adapter,configuration_changed:drift,client_hooks_disabled:blocked,...messageReceipt(adapter?.receipts)}
 }
 // Backward-compatible readback for an existing v1 Codex installation.
 if(profile?.version===1&&profile.codex_hooks){try{const doc=readHookDocument(profile.codex_hooks);states.codex={configured:EVENTS.every(event=>doc.hooks?.[event]?.some(group=>group.hooks?.some(hook=>hook.statusMessage===LABELS.codex))),can_manage:false,available:true,configuration_changed:false,...messageReceipt(profile.codex_receipts)}}catch{}}
 return states
}
export async function controlCapture(profile,action,agent='grok') {
 const adapter=adapterFor(profile,agent)
 if(!adapter||!['enable','disable'].includes(action)||!CAPTURE_AGENTS.includes(agent))throw Error('capture_control_unavailable')
 const key=agent+':'+adapter.home
 if(inflight.has(key))return inflight.get(key)
 const operation=(async()=>{
  let status='failed'
  const audit=openSync(profile.control_receipts,constants.O_WRONLY|constants.O_CREAT|constants.O_APPEND|constants.O_NOFOLLOW,0o600)
  try{const stat=fstatSync(audit);if(!stat.isFile()||stat.uid!==process.getuid()||stat.mode&0o077)throw Error('unsafe_control_receipts')}finally{closeSync(audit)}
  try{
   const args=['-I','-B',adapter.installer,'--device-binding',adapter.device_binding,'--receipts',adapter.receipts,'--'+agent+'-home',adapter.home,...(agent==='codex'?[]:['--launcher',adapter.launcher]),'--confirm-user-scope',...(action==='disable'?['--disable']:[])]
   const reply=await execute(adapter.python,args,{timeout:15000,maxBuffer:8192,env:{PATH:'/usr/bin:/bin',HOME:process.env.HOME??''}})
   const result=JSON.parse(reply.stdout);if(!['installed','unchanged','disabled'].includes(result.status))throw Error('invalid_installer_result')
   status=result.status;return {status}
  }finally{
   const fd=openSync(profile.control_receipts,constants.O_WRONLY|constants.O_CREAT|constants.O_APPEND|constants.O_NOFOLLOW,0o600)
   try {const stat=fstatSync(fd);if(!stat.isFile()||stat.uid!==process.getuid()||stat.mode&0o077)throw Error('unsafe_control_receipts');writeSync(fd,JSON.stringify({agent,action,status,checked_at:new Date().toISOString()})+'\n')}finally{closeSync(fd)}
  }
 })()
 inflight.set(key,operation)
 try{return await operation}finally{inflight.delete(key)}
}
