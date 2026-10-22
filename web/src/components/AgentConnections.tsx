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
    <DialogTrigger asChild><Button variant="outline" size="sm"><Plug size={15} aria-hidden="true" />Agent 接入</Button></DialogTrigger>
    <DialogContent className="agent-connections">
      <DialogHeader><DialogTitle>配置本机 Agent 接入</DialogTitle>
        <DialogDescription>检测到客户端后，点击“一键配置 Hook”完成用户级接入。配置后，新会话会自动进入此看板，无需逐个设置项目。</DialogDescription></DialogHeader>
      <div className="agent-discovery-status"><p role="status">{loading ? '正在检测…' : status || `检测到 ${agents.filter(agent => agent.detected).length} 个客户端`}</p>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => refresh()}>重新检测</Button></div>
      <ul className="agent-inventory">{rows(agents.filter(agent => agent.detected))}</ul>
      {agents.some(agent => !agent.detected) ? <details><summary>其他客户端 · 常见位置未检测到</summary>
        <ul className="agent-inventory">{rows(agents.filter(agent => !agent.detected))}</ul>
      </details> : null}
      <div className="agent-setup-explanation"><strong>Hook 自动采集 · Skill 按需执行</strong><p>记录新会话需要 Hook，不需要调用 Skill。配置只记录项目、会话和消息；历史对话不回填，也不会自动开启规则审批、Context 注入或 Memory 写入。</p></div>
      <details><summary>配置会修改什么？</summary>
        <p>Codex 写入 ~/.codex/hooks.json，Claude Code 写入 ~/.claude/settings.json，Grok 写入 ~/.grok/hooks/jasmine-conversation.json。安装前备份已有配置，保留其他 Hook；停用只移除 Jasmine 自己的采集项，已有对话保留。</p>
        <p>点击后，客户端原有的信任审查仍由客户端负责。Codex 需要在 /hooks 审查信任；Claude 的工作目录信任和禁用选项照常生效。新开会话后发送普通消息即可，助手回复完成时同步到看板。</p>
      </details>
      <details><summary>看板中的项目来自哪里？</summary><p>这里只展示当前 Core 的登记记录，包括已接入 Agent 使用过的工作目录，以及手动或系统创建的项目。不是整机项目扫描；不同工作副本按各自目录登记。</p></details>
    </DialogContent>
  </Dialog>
}
