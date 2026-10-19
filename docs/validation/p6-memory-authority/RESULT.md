# P6 历史 Memory / 当前 Authority：实现与隔离验证

日期：2026-10-18（America/Los_Angeles）。状态：**IMPLEMENTED / AUTHOR_SCOPED_VALIDATION_PASS**。
实现及下述验证由同一代理完成，**独立复核/签署未运行**；不代表整个 P6、P4 或 V1 通过。

基线：`66071992e7764147f51863f70509a03a6e789eb3`。
功能与真实浏览器验证绑定候选：`eb64ee19dd5f15de0324a1d3738ccb9c3b2175fd`，分支 `p6-memory-authority`。
本结果之后的文档提交不改变已验证功能源码或重绑定旧证据。

## 本次交付

Memory 条目详情新增历史来源版本与当前 Rule 记录的只读对照，突出显示来源已替代/撤销，
列出原文、版本、当前状态、Core 采样时间及来源/状态变更 Event 深链。
Scope、Rule 或版本不完整时明确无法核对；仅 memory:read 的凭据不获得当前 Rule 快照。
Core 以一次 SELECT 联查版本与状态，不按自然语言猜测冲突，不调用引擎，不写 Truth/Memory/jobs。

Trace 展示已有的 superseded_decision / retired_decision 原因和回到 Memory 的链接，
明确这是召回当时的过滤记录，不能当成当前即时判定。保留原始 filtered JSON 和当时 Authority 引用。
`recallable`/ACTIVE 仅表示生命周期；置顶不会绕过原有召回过滤。没有变更 Guard、召回策略或 schema。

规则版本一致仅表示关联的版本仍 ACTIVE，**不是文字语义一致、没有冲突或验证 PASS**。
没有 origin_rule_id/version 的历史文本不会被自动判定为安全；任意跨 Rule 的语义矛盾检测不在本切片。

## 验证

| 验证项 | 结果与边界 |
| --- | --- |
| 新增后端检查 | 14 项通过，含真实 socket API 的权限/ACL；单 SELECT、只读完整库比较、缺失/外国项目与任务关联、替代/撤销 |
| Memory 相关回归 | 517 项，0 失败，1 项既有跳过；后端源码此后未变化 |
| 前端 | 最终候选全量 560 项通过（含新增 11 项），生产构建通过 |
| 真实 Core / Chrome | 最终 gate-03 的 20 项服务/业务检查 + 浏览器 live 16 / readonly 11 项全部通过 |
| 旧记忆与新规则 | 初始匹配→通过 Core API 替代→实际点击重新读取→v1/v2 对照；旧 Memory 原文/状态不改，Rule 和 Event 深链及刷新可读 |
| 原有过滤 | 真实 Core MemoryAdapter + 明确标注的 FakeTransport：已替代/已撤销决策均被过滤，pinned 后仍无召回项；不宣称模型已收到 Context |
| 只读不改数据 | 浏览器 readonly 阶段，rules/versions/changes、events、tasks/steps、memory/items/sources/jobs/attempts 的内容摘要完全不变 |
| 日常环境 | 19 个配置/启动脚本/plist hash 与 4 个监听 PID 前后相同；日常仍为 c37709b、健康 schema12 |
| 清理 | 所有本次演练 Core、看板及 Chrome 已停止，私有数据与证据保留 |

HTTP 正常认证使用记录及审计允许追加；“只读”不宣称 audit/API-key 使用时间或 DB 文件逐字节不变。
浏览器由自动化操作，不是物理人工签署。Sources 是显式标记的 synthetic fixture；
其中未关联条目由 store 直接构造为缺失数据用例。Worker/recall 引擎边界为 FakeTransport，
没有连接日常或真实 Hindsight、没有调用生成/reflect、没有启动已停止的收益实验。

## 保留的失败及现有边界

- 初版新增字段把 datetime 对象直接送入 JSON，真实 HTTP 测试发现序列化断开；已改为 RFC3339，原失败日志保留。
- ACL 缺少授权的既有响应为 project_not_found，未知 Memory 为 memory_not_found；本轮保持这两个既有错误码，均不附加对照数据。不宣称整个旧 API 已具备不可区分的错误响应。
- gate-01：核心 live 路径通过，最后的低权限测试 token 缺少看板登录需要的 objects:read，测试停在登录。只修测试 token 后 gate-02 通过。失败目录不覆盖、不冒充 P7 真实 FAIL Run。
- 视觉检查发现截图停在重新读取按钮处，告警在视口之外；滚动到真实告警区域并增加警示样式后，干净候选 gate-03 通过。没有改动截图内容或旧证据。
- 原生 Agent、真实 Hindsight 故障/恢复与召回收益、完整 P6/P7/V1 未在本轮验收。没有 push/PR/merge/deploy，没有改日常配置、用户 hooks 或信任。

## 复现与证据

在干净候选 checkout 安装 web/package-lock 对应依赖并执行 npm run build 后：

```sh
PYTHONDONTWRITEBYTECODE=1 /path/to/context-python scripts/p6/memory_authority_gate.py --out /absolute/new/private/run-dir
```

context-python 含既有 tiktoken 0.14.0。`--daily-base` 可选，仅比对日常服务指纹和健康；
健康 GET 会正常追加审计。输出必须是新目录。凭据只在内存/子进程 stdin，Chrome 私有 profile 结束删除。

受限机器证据索引：[manifest.json](manifest.json)。本机私有最终回执：
`/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/gate-03/gate.json`；完整浏览器回执为同目录 browser-live.json / browser-readonly.json。
源码与截图/日志 hash 由私有 EVIDENCE-MANIFEST.json 核验。未公开 DB、token 或原始私有数据。

下一状态是独立代码复核与本切片证据复验；此前保持作者验证状态，不提升为 SIGNED_SCOPED_ACCEPTANCE。


后续界面阅读层级优化见 [COMPACT-RESULT.md](COMPACT-RESULT.md)，本文件仍绑定原功能候选，不追溯改写旧验证。
