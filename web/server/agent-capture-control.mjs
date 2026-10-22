/** Operator-pinned installation only. Browser input cannot choose code, paths or keys. */
import { constants, openSync, closeSync, fstatSync, readFileSync, existsSync, writeSync, readSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { createHash, timingSafeEqual } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
const execute = promisify(execFile)
const LABEL='Jasmine daily Grok conversation capture'
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
  if(profile.version!==1||profile.enabled!==true||!/^[a-f0-9]{64}$/.test(profile.browser_token_sha256))return null
  for(const key of ['python','installer','launcher','device_binding','receipts','grok_home','control_receipts'])if(typeof profile[key]!=='string'||!isAbsolute(profile[key])||profile[key].includes('\0'))return null
  return profile
 }catch{return null}finally{if(fd!==undefined)closeSync(fd)}
}
export function canManageCapture(profile,token) {
 if(!profile||!token?.startsWith('Bearer '))return false
 const digest=createHash('sha256').update(token.slice(7)).digest()
 const expected=Buffer.from(profile.browser_token_sha256,'hex');return expected.length===digest.length&&timingSafeEqual(digest,expected)
}
function expectedCommand(profile){return [profile.python,'-I','-B',profile.launcher,'--device-binding',profile.device_binding,'--receipts',profile.receipts,'--grok-home',profile.grok_home].map(quoted).join(' ')}
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
 let configured=false,drift=false
 if(profile){
  const file=join(profile.grok_home,'hooks/jasmine-conversation.json')
  try{
   const doc=readHookDocument(file)
   configured=doc.description===LABEL&&EVENTS.every(event=>doc.hooks?.[event]?.length===1&&doc.hooks[event][0].hooks?.length===1&&doc.hooks[event][0].hooks[0].command===expectedCommand(profile))
   drift=!configured
  }catch{drift=existsSync(file)}
 }
 let codex
 if(profile?.codex_hooks){try{const doc=readHookDocument(profile.codex_hooks);codex={configured:EVENTS.every(event=>doc.hooks?.[event]?.some(group=>group.hooks?.some(hook=>hook.statusMessage==='Jasmine daily conversation capture'))),can_manage:false,available:true,configuration_changed:false,...messageReceipt(profile.codex_receipts)}}catch{}}
 return {...(codex?{codex}:{}),grok:{configured,can_manage:canManageCapture(profile,authorization),available:!!profile,configuration_changed:drift,...messageReceipt(profile?.receipts)}}
}
export async function controlCapture(profile,action) {
 if(!profile||!['enable','disable'].includes(action))throw Error('capture_control_unavailable')
 const key=profile.grok_home
 if(inflight.has(key))return inflight.get(key)
 const operation=(async()=>{
  let status='failed'
  const audit=openSync(profile.control_receipts,constants.O_WRONLY|constants.O_CREAT|constants.O_APPEND|constants.O_NOFOLLOW,0o600)
  try{const stat=fstatSync(audit);if(!stat.isFile()||stat.uid!==process.getuid()||stat.mode&0o077)throw Error('unsafe_control_receipts')}finally{closeSync(audit)}
  try{
   const args=['-I','-B',profile.installer,'--device-binding',profile.device_binding,'--receipts',profile.receipts,'--grok-home',profile.grok_home,'--launcher',profile.launcher,'--confirm-user-scope',...(action==='disable'?['--disable']:[])]
   const reply=await execute(profile.python,args,{timeout:15000,maxBuffer:8192,env:{PATH:'/usr/bin:/bin',HOME:process.env.HOME??''}})
   const result=JSON.parse(reply.stdout);if(!['installed','unchanged','disabled'].includes(result.status))throw Error('invalid_installer_result')
   status=result.status;return {status}
  }finally{
   const fd=openSync(profile.control_receipts,constants.O_WRONLY|constants.O_CREAT|constants.O_APPEND|constants.O_NOFOLLOW,0o600)
   try {const stat=fstatSync(fd);if(!stat.isFile()||stat.uid!==process.getuid()||stat.mode&0o077)throw Error('unsafe_control_receipts');writeSync(fd,JSON.stringify({action,status,checked_at:new Date().toISOString()})+'\n')}finally{closeSync(fd)}
  }
 })()
 inflight.set(key,operation)
 try{return await operation}finally{inflight.delete(key)}
}
