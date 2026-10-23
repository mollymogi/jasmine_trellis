import { AgentLogo } from './AgentLogo'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { processingAgent, processingPreview, processingState, processingTime, type ProcessingItem } from '../lib/processingPresentation'
import { formatTimestamp } from '../lib/text'

/** Human list numbers are positions in this view, never replacement source IDs. */
export function ProcessingRecords({items,offset,onDetails}:{items:ProcessingItem[];offset:number;onDetails:(item:ProcessingItem)=>void}) {
  return <div className="processing-records"><table aria-label="对话提炼记录">
    <thead><tr><th>序号</th><th>时间</th><th>Agent</th><th>来源</th><th>内容摘要</th><th>状态</th><th><span className="sr-only">操作</span></th></tr></thead>
    <tbody>{items.map((item,index)=>{
      const agent=processingAgent(item),state=processingState(item)
      return <tr key={item.event_id} data-source-event={item.event_id}>
        <td data-label="序号">{offset+index+1}</td>
        <td data-label="时间"><time dateTime={item.queued_at} title={formatTimestamp(item.queued_at)}>{processingTime(item.queued_at)}</time></td>
        <td data-label="Agent"><span className="processing-agent"><AgentLogo agent={agent.id} size={20}/>{agent.name}</span></td>
        <td data-label="来源"><span className="processing-project">{item.project_name||'未命名项目'}</span><span className="processing-role">{item.event_type==='assistant.message'?'助手回复':'用户消息'}</span></td>
        <td data-label="内容摘要"><button className="processing-record-title" onClick={()=>onDetails(item)}>{processingPreview(item)}</button></td>
        <td data-label="状态"><Badge variant={state.tone} data-status={item.status}>{state.label}</Badge></td>
        <td data-label="操作"><Button variant="ghost" size="sm" onClick={()=>onDetails(item)}>查看详情</Button></td>
      </tr>
    })}</tbody>
  </table></div>
}
