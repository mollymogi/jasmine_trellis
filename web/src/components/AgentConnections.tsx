import { useEffect, useRef, useState } from 'react'
import { AgentCaptureAction } from './AgentCaptureAction'
import { AgentLogo } from './AgentLogo'
import { Plug } from 'lucide-react'
import { useAgentDiscovery, type AgentDiscovery, type DetectedAgent } from '../app/useAgentDiscovery'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogTrigger } from './ui/dialog'

export function AgentConnections({ discovery }: { discovery?: AgentDiscovery }) {
  const [open, setOpen] = useState(false)
  const local = useAgentDiscovery(!discovery && open)
  const { agents, loading, status, refresh } = discovery ?? local
  const refreshing=useRef(false);refreshing.current=loading
  useEffect(() => {
    if (!open) return
    if (discovery) refresh()
    const timer=setInterval(()=>{if(!refreshing.current)refresh()},3000)
    return () => clearInterval(timer)
  }, [open, refresh, !!discovery])
  function rows(items: DetectedAgent[]) {
    return items.map(agent => <li key={agent.id}>
      <div className="agent-inventory-identity"><AgentLogo agent={agent.id} size={22} /><div><strong>{agent.name}</strong><small>{agent.detected ? [agent.cli ? 'CLI' : '', agent.desktop ? '桌面应用' : ''].filter(Boolean).join(' · ') + ' 已找到' : '常见位置未检测到'}</small></div></div>
      <AgentCaptureAction agent={agent} onChanged={refresh} refreshing={loading} />
    </li>)
  }
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><Button variant="ghost" size="sm"><Plug size={15} aria-hidden="true" />Agent 接入</Button></DialogTrigger>
    <DialogContent className="agent-connections">
      <DialogHeader><DialogTitle>本机 Agent 接入</DialogTitle>
        <DialogDescription>此设备的用户级接入。启用后，任意项目的新会话将自动进入看板；已有历史不回填。</DialogDescription></DialogHeader>
      <div className="agent-discovery-status"><p role="status">{loading ? '正在检测…' : status || `检测到 ${agents.filter(agent => agent.detected).length} 个客户端`}</p>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => refresh()}>重新检测</Button></div>
      <ul className="agent-inventory">{rows(agents.filter(agent => agent.detected))}</ul>
      {agents.some(agent => !agent.detected) ? <details><summary>其他客户端 · 常见位置未检测到</summary>
        <ul className="agent-inventory">{rows(agents.filter(agent => !agent.detected))}</ul>
      </details> : null}
      <details><summary>其他 Agent 怎样接入？</summary>
        <p>Codex 与 Grok 已提供原生采集适配器。点击 Grok 旁的“启用采集”，将安装 Jasmine 自己的 SessionStart、UserPromptSubmit、Stop、SessionEnd 用户级 Hook，发送到此正式 Core。无需修改项目或改变启动命令。</p>
        <p>“已启用”只代表配置已安装；“已收到消息”表示已收到原生写入回执。Grok 的用户级 Hook 按其自身规则加载，不修改信任记录。Claude、OpenCode 等仍需对应适配器。</p>
        <p>停用只停止后续采集，保留已有对话。采集只记录对话，不自动批准规则、注入 Context 或将整段聊天写入 Memory。</p>
      </details>
      <details><summary>看板中的项目来自哪里？</summary><p>这里只展示当前 Core 的登记记录，包括已接入 Agent 使用过的工作目录，以及手动或系统创建的项目。不是整机项目扫描；不同工作副本按各自目录登记。</p></details>
    </DialogContent>
  </Dialog>
}
