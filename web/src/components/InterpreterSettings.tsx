import { useEffect, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { useSession } from '../app/session'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from './ui/dialog'

type Settings={model:string;revision:number;models:{id:string;label:string}[];can_manage:boolean}
export function InterpreterSettings(){
 const {token}=useSession()
 const [open,setOpen]=useState(false),[value,setValue]=useState<Settings|null>(null),[model,setModel]=useState(''),[loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[reload,setReload]=useState(0)
 useEffect(()=>{
  if(!open)return
  const abort=new AbortController();setLoading(true);setValue(null);setError('');setNotice('')
  void fetch('/_local/interpreter',{headers:token?{Authorization:`Bearer ${token}`}:{},cache:'no-store',signal:abort.signal})
   .then(async r=>{if(!r.ok)throw Error(r.status===401||r.status===403?'当前凭证无法读取模型设置。':'本机模型设置暂不可用，请重新读取。');return r.json()})
   .then(v=>{if(!Array.isArray(v.models)||!v.models.some((m:{id:string})=>m.id===v.model))throw Error('模型目录暂不可用。');if(!abort.signal.aborted){setValue(v);setModel(v.model)}})
   .catch(e=>{if(!abort.signal.aborted)setError(e instanceof Error?e.message:'读取失败。')})
   .finally(()=>{if(!abort.signal.aborted)setLoading(false)})
  return()=>abort.abort()
 },[open,token,reload])
 async function save(){
  if(!value)return
  setBusy(true);setError('');setNotice('')
  try{
   const r=await fetch('/_local/interpreter',{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify({model,expected_revision:value.revision})})
   if(!r.ok)throw Error(r.status===409?'设置已在另一处更新，请重新读取后再选择。':r.status===403?'当前凭证没有修改模型的权限。':'模型设置未完成，请重新读取确认当前选择。')
   const next=await r.json();setValue(next);setModel(next.model);setNotice('已保存，之后开始的分析将使用此模型。')
  }catch(e){setError(e instanceof Error?e.message:'保存失败。')}finally{setBusy(false)}
 }
 return <Dialog open={open} onOpenChange={setOpen}>
  <DialogTrigger asChild><Button size="sm" variant="outline"><SlidersHorizontal size={14}/>模型设置</Button></DialogTrigger>
  <DialogContent><DialogHeader><DialogTitle>提炼模型</DialogTitle><DialogDescription>选择分析对话内容的模型。与会话中 Agent 的模型、记忆引擎的模型分开设置。</DialogDescription></DialogHeader>
   <p className="muted">沿用本机 Codex 登录。待提炼消息会发送给所选 OpenAI 模型。</p>
   {loading?<p role="status">正在读取模型设置…</p>:null}
   {value?<><label htmlFor="interpreter-model">用于对话提炼的模型</label><select id="interpreter-model" value={model} disabled={busy||!value.can_manage} onChange={e=>{setModel(e.target.value);setNotice('')}}>{value.models.map(m=><option value={m.id} key={m.id}>{m.label}</option>)}</select>
    <p className="muted">只影响之后开始的分析，已有结果和正在处理的内容不变。调用失败会显示失败，不会自动换模型。</p>
    <details><summary>模型范围与额度</summary><p>列表来自本机提炼专用目录；能否调用取决于当前 Codex 账号权限和额度。这里不会修改 Codex 或 Claude 的日常模型、登录或代理设置。</p></details>
    {!value.can_manage?<p>当前凭证只可查看模型设置。</p>:null}</>:null}
   {error?<p role="alert">{error}</p>:null}{notice?<p role="status">{notice}</p>:null}
   <div className="processing-actions"><Button variant="outline" disabled={busy||loading} onClick={()=>setReload(n=>n+1)}>重新读取</Button><Button disabled={busy||loading||!value?.can_manage||model===value.model} onClick={()=>void save()}>{busy?'正在保存…':'保存模型'}</Button></div>
  </DialogContent>
 </Dialog>
}
