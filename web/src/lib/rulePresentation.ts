import type { Rule } from '../api/types'
import { maskCredentials } from './text'
export const RULE_STATES:Record<string,string>={ACTIVE:'已批准',PROPOSED:'待批准',RETIRED:'已停用',SUPERSEDED:'历史版本'}
export const RULE_USES:Record<string,string>={CONTEXT:'上下文约定',DENY:'禁止执行',CONFIRM:'执行前确认',VERIFY:'验收检查'}
export const RULE_KINDS:Record<string,string>={RULE:'约定',DECISION:'决策',ACCEPTANCE:'验收要求'}
export const TOOL_LABELS:Record<string,string>={shell:'终端命令','file.read':'读取文件','file.write':'写入文件','file.edit':'编辑文件',web:'网页访问',mcp:'MCP 工具',other:'其他工具'}
export function rulePreview(rule:Rule){return maskCredentials(rule.content).text}
export function ruleTarget(rule:Rule){
 const parts:string[]=[]
 if(rule.matcher.tool_category)parts.push(TOOL_LABELS[String(rule.matcher.tool_category)]??String(rule.matcher.tool_category))
 if(rule.matcher.tool)parts.push(String(rule.matcher.tool))
 if(rule.matcher.action)parts.push(`操作：${String(rule.matcher.action)}`)
 if(rule.matcher.path_prefix)parts.push(`路径：${String(rule.matcher.path_prefix)}`)
 return parts.length?parts.join(' · '):'没有额外的工具或路径条件'
}
