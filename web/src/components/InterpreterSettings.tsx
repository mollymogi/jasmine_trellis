import { useEffect, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { useSession } from '../app/session'
import { AgentLogo } from './AgentLogo'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from './ui/dialog'

type Model={id:string;label:string}
type Client={id:string;label:string;detected:boolean;enabled?:boolean;models:Model[]}
type Settings={provider?:string;model:string;revision:number;models:Model[];providers?:Client[];can_manage:boolean}
const failure=(code:string)=>code==='provider_login_required'?'此客户端的登录已失效，请先在客户端重新登录。':code==='provider_rate_limited'?'此模型的额度不足或调用频率受限。':code==='provider_timeout'?'验证超时，请稍后重试。':'客户端未完成提炼，请检查该客户端的模型服务是否可用。'
export function InterpreterSettings(){
 const {token}=useSession()
 const [open,setOpen]=useState(false),[value,setValue]=useState<Settings|null>(null),[provider,setProvider]=useState('codex'),[model,setModel]=useState(''),[loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[checking,setChecking]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[reload,setReload]=useState(0)
 const headers=()=>({'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})})
 useEffect(()=>{
  if(!open)return
  const abort=new AbortController();setLoading(true);setValue(null);setError('');setNotice('')
  void fetch('/_local/interpreter',{headers:token?{Authorization:`Bearer ${token}`}:{},cache:'no-store',signal:abort.signal})
   .then(async r=>{if(!r.ok)throw Error(r.status===401||r.status===403?'当前凭证无法读取提炼器设置。':'本机提炼器设置暂不可用，请重新读取。');return r.json()})
   .then(v=>{if(!Array.isArray(v.models)||!v.models.some((m:Model)=>m.id===v.model))throw Error('模型目录暂不可用。');if(!abort.signal.aborted){setValue(v);setProvider(v.provider??'codex');setModel(v.model)}})
   .catch(e=>{if(!abort.signal.aborted)setError(e instanceof Error?e.message:'读取失败。')})
   .finally(()=>{if(!abort.signal.aborted)setLoading(false)})
  return()=>abort.abort()
 },[open,token,reload])
 const clients=value?.providers??[{id:'codex',label:'Codex',detected:true,enabled:true,models:value?.models??[]}]
 const client=clients.find(p=>p.id===provider),locked=busy||checking||!value?.can_manage
 async function save(){
  if(!value)return
  setBusy(true);setError('');setNotice('')
  try{
   const r=await fetch('/_local/interpreter',{method:'POST',headers:headers(),body:JSON.stringify({...(value.providers?{provider}:{}),model,expected_revision:value.revision})})
   if(!r.ok)throw Error(r.status===409?'设置已在另一处更新，请重新读取后再选择。':r.status===403?'当前凭证没有修改提炼器的权限。':'提炼器设置未完成，请重新读取确认当前选择。')
   const next=await r.json();setValue(next);setProvider(next.provider??'codex');setModel(next.model);setNotice('已保存，之后开始的分析将使用此客户端和模型。')
  }catch(e){setError(e instanceof Error?e.message:'保存失败。')}finally{setBusy(false)}
 }
 async function check(){
  setChecking(true);setError('');setNotice('')
  try{
   const r=await fetch('/_local/interpreter',{method:'POST',headers:headers(),body:JSON.stringify({action:'test',provider,model})})
   if(!r.ok)throw Error('暂时无法验证，请稍后重试。')
   const result=await r.json();if(result.status!=='passed')throw Error(failure(result.error_code))
   setNotice(`${client?.label} · ${model} 验证通过。尚未保存时，当前提炼器不会改变。`)
  }catch(e){setError(e instanceof Error?e.message:'验证失败。')}finally{setChecking(false)}
 }
 return <Dialog open={open} onOpenChange={next=>{if(!busy&&!checking)setOpen(next)}}>
  <DialogTrigger asChild><Button size="sm" variant="outline"><SlidersHorizontal size={14}/>提炼器设置</Button></DialogTrigger>
  <DialogContent className="interpreter-settings"><DialogHeader><DialogTitle>选择提炼器</DialogTitle><DialogDescription>用本机已安装的 AI 客户端分析对话，选择后保存即可应用。</DialogDescription></DialogHeader>
   {loading?<p role="status">正在读取提炼器…</p>:null}
   {value?<><div className="interpreter-fields"><div><label htmlFor="interpreter-client">提炼客户端</label><select id="interpreter-client" value={provider} disabled={locked} onChange={e=>{const next=clients.find(p=>p.id===e.target.value)!;setProvider(next.id);setModel(next.id===(value.provider??'codex')?value.model:next.models[0]?.id??'');setNotice('');setError('')}}>{clients.map(p=><option key={p.id} value={p.id} disabled={!p.detected||p.enabled===false||!p.models.length}>{p.label}{!p.detected?' · 未检测到':p.enabled===false?' · 暂未启用':!p.models.length?' · 无可用模型目录':''}</option>)}</select></div>
    <div><label htmlFor="interpreter-model">用于对话提炼的模型</label><select id="interpreter-model" value={model} disabled={locked} onChange={e=>{setModel(e.target.value);setNotice('');setError('')}}>{client?.models.map(m=><option value={m.id} key={m.id}>{m.label}</option>)}</select></div></div>
    <p className="interpreter-client-note"><AgentLogo agent={provider} size={20}/><span>沿用 {client?.label} 的本机登录，对话内容会发送给该客户端所选模型的服务方。</span></p>
    <details><summary>生效范围与验证</summary><p>只影响之后开始的分析，已有结果和正在处理的内容不变。调用失败不会自动切换客户端。模型列表不代表账号已有使用权限。</p><p>验证会发送一条测试问候，不使用真实对话，也不写入记忆。提炼时关闭工具、Hook 和项目指令；不会修改客户端的日常模型、登录或代理设置。</p></details>
    {!value.can_manage?<p>当前凭证只可查看提炼器设置。</p>:null}</>:null}
   {error?<p role="alert">{error}</p>:null}{notice?<p role="status">{notice}</p>:null}
   <div className="interpreter-footer"><Button variant="ghost" disabled={busy||checking||loading} onClick={()=>setReload(n=>n+1)}>重新读取</Button><div className="processing-actions">{value?.providers?<Button variant="outline" disabled={locked||loading||!model} onClick={()=>void check()}>{checking?'正在验证…':'验证可用性'}</Button>:null}<Button disabled={locked||loading||!model||(model===value?.model&&provider===(value?.provider??'codex'))} onClick={()=>void save()}>{busy?'正在保存…':'保存设置'}</Button></div></div>
  </DialogContent>
 </Dialog>
}
