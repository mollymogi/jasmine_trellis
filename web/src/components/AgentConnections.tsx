import { useState } from 'react'
import { AgentLogo } from './AgentLogo'
import { Plug } from 'lucide-react'
import { useAgentDiscovery, type AgentDiscovery, type DetectedAgent } from '../app/useAgentDiscovery'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogTrigger } from './ui/dialog'

export function AgentConnections({ discovery }: { discovery?: AgentDiscovery }) {
  const [open, setOpen] = useState(false)
  const local = useAgentDiscovery(!discovery && open)
  const { agents, loading, status, refresh } = discovery ?? local
  function rows(items: DetectedAgent[]) {
    return items.map(agent => <li key={agent.id}>
      <div className="agent-inventory-identity"><AgentLogo agent={agent.id} size={22} /><div><strong>{agent.name}</strong><small>{agent.detected ? [agent.cli ? 'CLI' : '', agent.desktop ? '桌面应用' : ''].filter(Boolean).join(' · ') + ' 已找到' : '常见位置未检测到'}</small></div></div>
      <span>{agent.capture_supported ? '已有采集适配器' : '待开发采集适配器'}</span>
    </li>)
  }
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><Button variant="ghost" size="sm"><Plug size={15} aria-hidden="true" />Agent 接入</Button></DialogTrigger>
    <DialogContent className="agent-connections">
      <DialogHeader><DialogTitle>本机 Agent 接入</DialogTitle>
        <DialogDescription>检测看板所在设备的常见安装位置。找到客户端后，仍需配置采集才能看到对话。</DialogDescription></DialogHeader>
      <div className="agent-discovery-status"><p role="status">{loading ? '正在检测…' : status || `检测到 ${agents.filter(agent => agent.detected).length} 个客户端`}</p>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => refresh()}>重新检测</Button></div>
      <ul className="agent-inventory">{rows(agents.filter(agent => agent.detected))}</ul>
      {agents.some(agent => !agent.detected) ? <details><summary>其他客户端 · 常见位置未检测到</summary>
        <ul className="agent-inventory">{rows(agents.filter(agent => !agent.detected))}</ul>
      </details> : null}
      <details><summary>其他 Agent 怎样接入？</summary>
        <p>当前自动采集适配器支持 Codex。检测到的 Grok 客户端尚缺对应的会话采集适配器，并非客户端检测失败。其他 Agent 需要接收它自己的会话开始、用户输入、助手回复与会话结束事件，再把工作目录、会话标识及来源写入 Core。仅安装客户端或手动创建项目，不会采集聊天记录。</p>
        <p>适配器安装后，还需按客户端要求启用或信任 Hook / 插件，并用一条真实消息验证。检测不会执行客户端、修改配置或自动登记项目。</p>
      </details>
      <details><summary>看板中的项目来自哪里？</summary><p>这里只展示当前 Core 的登记记录，包括已接入 Agent 使用过的工作目录，以及手动或系统创建的项目。不是整机项目扫描；不同工作副本按各自目录登记。</p></details>
    </DialogContent>
  </Dialog>
}
