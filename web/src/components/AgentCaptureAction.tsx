import { useState } from 'react'
import { useSession } from '../app/session'
import type { DetectedAgent } from '../app/useAgentDiscovery'
import { Button } from './ui/button'
export function captureLabel(agent: DetectedAgent) {
 if(!agent.capture_supported)return '暂不支持自动接入'
 if(agent.capture?.client_hooks_disabled)return '客户端已禁用所有 Hook'
 if(!agent.capture)return '已有采集适配器'
 if(agent.capture.configuration_changed)return '配置与安装版本不一致'
 if(!agent.capture.configured)return '尚未启用采集'
 if(agent.capture.last_error)return '最近一次采集失败'
 return agent.capture.last_event_id?'已启用 · 已收到消息':'已启用 · 等待首条消息'
}
export function AgentCaptureAction({agent,onChanged,refreshing}:{agent:DetectedAgent;onChanged:()=>void;refreshing:boolean}) {
 const {token}=useSession()
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState('')
 async function change(action:'enable'|'disable') {
  setBusy(true);setError('');setNotice('')
  try{
   const response=await fetch(`/_local/agents/${agent.id}/capture`,{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify({action})})
   if(!response.ok)throw Error(response.status===403?'当前凭证没有接入管理权限。':'未能完成接入操作，请检查本机配置后重试。')
   setNotice(action==='enable'?`已安装配置，请新开 ${agent.name} 会话${agent.id==='codex'?'并在 /hooks 审查信任后发送消息':'发送普通消息'}。`:'已停用，已有对话记录保留。');onChanged()
  }catch(error){setError(error instanceof Error?error.message:'接入操作失败。')}finally{setBusy(false)}
 }
 return <div className="agent-capture-action">
  <span>{captureLabel(agent)}</span>
  {agent.capture_supported&&agent.detected?<div className="agent-capture-buttons">
   <Button size="sm" variant={agent.capture?.configured?'outline':'default'} disabled={busy||refreshing||!agent.capture?.can_manage||(!agent.cli&&!agent.capture?.configured)||(!!agent.capture?.client_hooks_disabled&&!agent.capture?.configured)} onClick={()=>void change(agent.capture?.configured?'disable':'enable')}>
    {busy?'正在处理…':agent.capture?.configured?'停用 Hook':'一键配置 Hook'}
   </Button>
   {agent.capture?.can_manage&&agent.capture.configured&&(agent.capture.configuration_changed||agent.capture.last_error)?<Button size="sm" variant="outline" disabled={busy||refreshing} onClick={()=>void change('enable')}>重新安装</Button>:null}
  </div>:null}
  {agent.capture_supported&&agent.capture?.available===false?<small>本机接入管理尚未配置</small>:null}
  {agent.capture?.available&&!agent.capture.can_manage?<small>当前凭证仅可查看接入状态</small>:null}
  {agent.detected&&!agent.capture_supported?<small>已找到客户端；对话采集适配器尚未提供，不能自动配置。</small>:null}
  {agent.capture_supported&&!agent.cli?<small>需要本机 CLI；仅检测到桌面应用不能证明支持这些 Hook。</small>:null}
  {agent.id==='codex'&&agent.capture?.configured?<small>若尚未信任，请在 Codex /hooks 审查 Jasmine 的四项 Hook。</small>:null}
  {agent.capture?.client_hooks_disabled?<small>请先在 Claude 设置中恢复 Hook；不会替你覆盖全局禁用选项。</small>:null}
  {agent.capture?.last_message_at?<small>最近消息 {new Date(agent.capture.last_message_at).toLocaleString('zh-CN')}</small>:null}
  {notice?<small role="status">{notice}</small>:null}{error?<small role="alert">{error}</small>:null}
 </div>
}
