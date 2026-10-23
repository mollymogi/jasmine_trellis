import { BookOpen } from 'lucide-react'
import { Button } from './ui/button'
import { Dialog,DialogContent,DialogDescription,DialogHeader,DialogTitle,DialogTrigger } from './ui/dialog'
export function RuleGuide(){return <Dialog><DialogTrigger asChild><Button size="sm" variant="outline"><BookOpen size={14}/>如何使用</Button></DialogTrigger><DialogContent className="rules-dialog"><DialogHeader><DialogTitle>规则是当前要遵守的约定</DialogTitle><DialogDescription>记忆保存历史经验；规则保存当前已批准的要求。当前规则优先于历史记忆。</DialogDescription></DialogHeader>
 <div className="rule-guide-flow"><span><b>1</b> 从对话提炼或手动提出</span><span><b>2</b> 由你审核并批准</span><span><b>3</b> 按范围提供给 Agent</span></div>
 <dl className="rule-guide-list"><dt>适用范围</dt><dd>全局约定适用于所有项目；项目约定适用于该项目；任务约定只适用于指定任务。查看某个任务的生效规则时，也会包含全局和项目约定。</dd><dt>上下文约定</dt><dd>例如“项目文档使用中文”。规则可供上下文构建使用；Agent 是否收到，取决于是否接入上下文注入。</dd><dt>禁止执行 / 执行前确认</dt><dd>必须设置明确的工具、操作或路径条件，并为 Agent 接入执行前检查（Guard）。只写一句自然语言、或仅开启对话采集，不会自动形成拦截。</dd><dt>验收检查</dt><dd>用于检查要求和验收依据；标为验收检查不等于已经验证通过。</dd><dt>修改与停用</dt><dd>修改已批准的规则会追加新版本；停用后保留原有内容和历史，不会删除记录。待批准规则还不参与生效规则查询。</dd></dl>
 <p className="rules-note">“已批准”表示 Core 采用了这条规则，不代表 Agent 已收到或已开启拦截。</p>
 <Button asChild variant="outline"><a href="#/reviews/pending">前往待审核，确认对话提炼的建议</a></Button>
 </DialogContent></Dialog>}
