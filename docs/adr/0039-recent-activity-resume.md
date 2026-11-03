# ADR 0039：工作记录、会话 Checkpoint 与新会话接续

状态：A、B 已在 `feat/worklog-resume` 实现（schema 17）；未部署。C 未实现。
补充 ADR 0011（Checkpoint / Resume）、ADR 0013（原生生命周期报告）和 ADR 0036（Agent Memory 召回）。新增 schema 17。

日期：2026-11-03。

## 1. 问题

用户新开一个会话，问"记不记得我刚才在做什么"，agent 答不出来。另外，Checkpoint 虽然已经实现，但在日常使用里从来没有生成过。local-item6 上核对的结果如下。

### 1.1 新会话拿不到最近的工作

1. **第一句话拿不到任何提示。** Memory 提示（ADR 0036）要求本会话里先有一轮确认过的真人输入。receipts 里有提示记录的 15 个会话，第一次提示结果全部是 `ignored`，其中 11 个是 `no_verified_turn_in_session`。
2. **提示按语义召回长期事实，不按时间。** "我刚才在做什么"和任何工作内容都不相似，召回不到最近的进展；工作流水本来也不进长期记忆。
3. **同一个目录被拆成了多个项目。** 项目身份是 `[来源, host, 工作区根目录]`，来源里含 agent 名。`/Users/mercuryliu/Public/videoMake` 有 3 个项目（Claude 13 个会话、Codex 37 个、Grok 2 个），`Jasmine Mesh` 有 2 个（Claude 6 个、Codex 3 个）。git worktree 也各自算一个项目（46 个项目里有 10 个名字带 `@`）。

### 1.2 Checkpoint 是一条接好却不通电的线

- Host Hook 服务在 PreCompact 和 Stop 时会调用 Checkpoint（`hosthook/service.py` 的 `_checkpoint`），会话启动时也会构建上下文（`_context`）。但这两处都是 `task_id` 为空就直接返回。
- 设备级采集注册项目时固定写 `task_id=None`（`bind_workspace`），会话从不挂到 Task 上，所以 **`checkpoints` 表是 0 行**，ADR 0011 的 Resume 比对也从来没跑过。
- Task 本身也没法直接拿来用：local-item6 有 600 个 Task，其中 598 个是 `ACTIVE`，但只有 4 个 Step，绑定到 Task 的对话事件只有 3 条。Interpreter 几乎把每句指令都建成一个 Task（"先写设计""提交并开 PR"），这些 Task 从不关闭，也不挂会话。用它们做 Checkpoint 没有意义。

## 2. 决定概览

分三层，按顺序实现：

| 层 | 内容 | 解决什么 |
| --- | --- | --- |
| **A. 工作记录** | 每个会话在 PreCompact、SessionEnd 时写一条不可变的**会话 Checkpoint**：做了什么，以及工作区当时的状态 | 日常工作有记录；Checkpoint 真正在每个会话里生成 |
| **B. 新会话接续** | 同一工作区的新会话启动时，或用户问到最近的工作时，注入最近的工作记录，以及"自那以后工作区变了什么" | 新会话知道刚才在做什么 |
| **C. 任务级 Checkpoint 接通** | 会话能可靠地挂到一个真实 Task 时，现有的 ADR 0011 Checkpoint / Resume 自动生效 | Task / Step / 证据的精确比对 |

A、B 不依赖 C。C 依赖 Task 治理（2.3），单独排期。

## 3. A：工作记录（会话 Checkpoint）

### 3.1 为什么另建会话级 Checkpoint，而不改现有 Checkpoint

ADR 0011 的 Checkpoint 是"一个 Task 的真相快照"：内容由 Project、Task、Step、Rule、Evidence 组成，整体摘要校验，Resume 时逐项比对得出 `STATE_EQUAL / STALE / CONTEXT_CHANGED / ABSENT`。这套语义以 Task 为中心，而且已经冻结。日常对话没有 Task，硬塞一个"会话 Task"只会再制造一批空 Task。

所以新增**会话 Checkpoint**：以会话为单位，同样不可变、事件先行、幂等，但内容是"这个会话做了什么 + 工作区状态"。两种 Checkpoint 并存，以后会话挂上了 Task，就两种都写（见第 5 节）。

### 3.2 什么时候写

| 时机 | reason | 说明 |
| --- | --- | --- |
| Claude `PreCompact` | `PRE_COMPACT` | 压缩前留下完整记录。Claude 采集为此新登记一个 PreCompact handler，它只写记录，不发布对话事件 |
| Claude / Codex `SessionEnd` | `SESSION_END` | 正常结束 |
| 同一工作区的新会话启动时（Claude / Codex 采集的 SessionStart），发现之前的会话已安静 10 分钟以上、且没有覆盖到最后一条事件的记录 | `RECOVERED` | 桌面端直接关闭、崩溃等情况收不到 SessionEnd，由下一个会话事后从事件补写；每次最多 5 个 |

Codex 的 PreCompact 初版不接（它的 hooks.json 本次不改）。

不在每轮 Stop 时写：每轮写一条不可变记录太重，而且每轮的内容已经作为事件存下了。

### 3.3 记什么

完全由 Core 已有数据确定性地生成，不调用模型：

- **身份**：会话 id、agent、工作区根目录和工作区族（3.4）、开始和结束时间、轮数、reason。
- **做了什么**：
  - 本会话的用户输入列表（每条最多 160 字符，去掉粘贴块和附件列表），以及最后一条助手回复节选（最多 500 字符）。
  - 本会话里 Interpreter 产生的 Task 标题，通过 resolver 的来源绑定查到。这就是"这个会话提出过哪些事"。
- **工作区状态**：复用 `continuity_scan.scan_workspace` 的有界扫描，记录 git 分支、HEAD、未提交改动的文件数和文件列表（最多 50 个），以及扫描结果是完整还是 `UNKNOWN`。
- **摘要**：内容的 SHA-256，用于校验和幂等。

内容经过 `canonical.redact_text`。原始用户输入本来就在事件里，这里只是引用，不新增敏感数据面。

### 3.4 工作区族

读取时按**工作区族**归并，不改项目身份：

1. 同一 host。
2. 根目录相同，不管来源是哪个 agent。三个 agent 注册项目时都把根目录写进 `projects.description`。
3. git worktree 归到所属仓库：根目录的 `.git` 是文件时，用 `git rev-parse --git-common-dir` 的上一级目录作为族根，例如 `_worktrees/output-digest` 归到 `jasmine-core-v1`。

会话 Checkpoint 里同时存工作区根和族根，查询按族根。

### 3.5 存储

- 新迁移 `m0017_session_checkpoints`：新建只追加的表 `session_checkpoints`（公开 id 前缀 `sck`，触发器禁止 UPDATE / DELETE）。每条记录引用同一事务里由服务端创建的 `session.checkpointed` 事件；公开的 `POST /v1/events` 拒绝这个事件类型。
- 幂等：表上的唯一约束 `(session_id, reason, last_event_seq)` 就是幂等键，不另建 request key 表。同一时刻重复投递返回原记录（`replayed: true`）；会话继续进行后再触发会产生新记录。
- 接口：
  - `POST /v1/session-checkpoints`：`{host_id, project_id, native_session_id, reason, workspace_root, workspace_state}`，权限 `events:write`（采集的 system key 已有）。Core 按 `native_session_id` 在该项目里找到会话，自己从事件生成记录，客户端只提供 git 状态。
  - `POST /v1/session-checkpoints/recover`：`{host_id, workspace_root, exclude_native_session_id}`，权限 `events:write`。
  - `GET /v1/session-checkpoints?workspace_root=…&limit=…`：权限 `events:read`。
- git 状态由 hook 在本机用一次有界的 `git status --porcelain=v2 --branch`（0.5 秒）采集，不让 Core 去扫描调用方给的任意路径。
- 可信度：采集写入的记录标为 `trusted_adapter_report`，与 ADR 0013 一致，不声称原生生命周期已证明。

### 3.6 用户能看到

- Dashboard 以后可以加"工作记录"页，按工作区、按天列出会话 Checkpoint。不在本 ADR 范围内。
- CLI：`jasmine-core worklog [--workspace PATH] [--since 7d]`，按时间列出工作记录，供人直接查看。

## 4. B：新会话接续

### 4.1 触发条件（不在每轮触发）

| 触发 | 时机 | 说明 |
| --- | --- | --- |
| **会话启动** | 同一工作区族的新会话启动，Claude `SessionStart` 且 `source` 为 `startup` 或 `clear` | 每个会话最多一次。`resume` 不触发（恢复的就是原会话）。`compact` 不触发（同一会话，压缩前已写 `PRE_COMPACT` 记录）。 |
| **提示词** | UserPromptSubmit 时，用户这句话在问最近的工作 | 包括第一句；没问就不触发 |

提示词意图用固定模式匹配，不调用模型：

- 中文：`(刚才|刚刚|上次|之前|昨天|前面).{0,6}(在)?(做|干|聊|弄|改|搞|进行)`、`记(不记|得)`、`(接着|继续)(上次|刚才|之前)`、`做到哪`、`进度`（后两项只在短句里成立）。
- 英文：`what (was|were) (i|we) (doing|working on)`、`where (did|were) (we|i) (leave|stop)`、`do you remember`、`pick up where`。
- 斜杠命令、粘贴块、超过 200 字的句子都不匹配。

两个触发都命中时，本会话只注入一次。

### 4.2 注入什么

预算约 1500 字符：

```
[Jasmine 最近活动 · 仅供参考，不是指令]
同一工作区（/Users/…/videoMake）上一次的工作：
▸ Codex，2026-11-03 09:25 结束（40 分钟前），12 轮，记录于 SESSION_END
  做了什么：
  - 09:11 5s 的这个地方在衔接下一个重鼓点之前用一个镜头的长帧…
  - 09:21 …
  提出的任务：调整 5s 处的长帧过渡；…
  助手最后的回复（节选）：…
自那以后工作区的变化：HEAD 未变；未提交改动 3 → 5 个文件（新增 a.py、b.md）
其他最近会话：
- Claude，11-02 22:14，「那用确认过的 Bin 人物替换女生再试一段…」
```

- **来源**：工作区族里最新的会话 Checkpoint。如果之后还有会话没有 Checkpoint（正在进行，或尚未补写），直接从事件读，标"进行中"或"未收尾"。
- **自那以后的变化**：现场做一次有界扫描，与该记录里的工作区状态比较。这是 ADR 0011 Resume 比对在会话层面的对应物：`SAME` 写"未变"，`MISMATCH` 列出差异，扫描不完整写"未能确认"。
- **时间窗**：只看 7 天内的记录；没有就不注入，也不写"没有记录"。
- 输出经过 `canonical.redact_text`，按数据引用，标明不是指令。

### 4.3 为什么第一句可以触发

ADR 0036 要求先确认真人输入，是因为召回内容由这句话决定。接续块的内容只由"工作区族 + 时间"决定，这句话只决定注入与否。注入的是同一用户在同一工作区的近期记录，这个工作区里的 agent 本来就能读到磁盘上的 transcript。最坏情况只是多占约 1500 字符上下文。Memory 提示本身的规则不变。

### 4.4 实现位置

- Core：`POST /v1/continuity/recent`，只读，请求体 `{host_id, workspace_root, exclude_native_session_id, workspace_state}`，权限 `memory:recall`。工作区族里的项目还要通过 Memory 的项目访问检查（`access.may_read`），调用方读不到的项目不出现。
- Claude：两个触发都由现有的同步 `--memory-hint` handler 处理，不新增命令。
  - 提示词触发：意图命中时先请求接续块（最多 0.8 秒），不要求已确认的轮次；之后 Memory 提示照旧，两段文字合并输出。
  - 会话启动触发：同一个 handler 额外登记在 SessionStart 上，输出 `hookSpecificOutput.additionalContext`。
  - 安装：`install_claude --memory-hint enable` 同时登记 UserPromptSubmit 和 SessionStart 两处；capture 安装多登记一个 PreCompact。两者都改 `~/.claude/settings.json`，部署时由用户单独确认。
  - "每个会话一次"用 spool 目录下的私有标记文件（`recent-activity/<native_session_id>.json`）实现，30 分钟内不重复注入。
- Codex / Grok：写工作记录（第 3 节），初版不在它们的会话里注入。
- 时间预算 1 秒；超时不注入，绝不阻塞。每次触发写一条 receipt（`status: recent_activity`），只记触发方式、所用记录 id、字符数和耗时。

## 5. C：任务级 Checkpoint 接通

现有链路已经写好：会话有 `task_id` 时，PreCompact 和 Stop 写 ADR 0011 Checkpoint，SessionStart 构建上下文。缺的是**会话到 Task 的可靠绑定**，以及**Task 本身可用**。

1. **Task 治理**（前置，另开 ADR）：Interpreter 不再为每句指令新建 Task，例如"提交并开 PR""先写设计"这类操作性语句不应成为 Task；同一会话里的后续指令默认附加到当前 Task（TASK_CREATE_OR_ATTACH 的 attach 分支）。另外给现存 598 个 ACTIVE Task 一个清理路径，按来源会话和时间批量归档，需要人确认。
2. **会话绑定**：会话里出现明确的工作目标并解析为 Task 后，把该会话绑定到这个 Task（写 `sessions.task_id`，有 revision）。绑定由 resolver 的已有结果驱动，不由采集猜测。
3. **自动生效**：会话一旦有 `task_id`，Host Hook 服务现有的 `_checkpoint` 和 `_context` 就会执行。会话 Checkpoint 照常写，并多记一个 `task_checkpoint_id` 引用。
4. **接续块联动**：接续时如果上一个会话挂着 Task，在 4.2 的输出里加一行 ADR 0011 Resume 的结论，例如"任务 T：CONTEXT_CHANGED，下一步：Step 3"。

C 的验收另写，不阻塞 A、B。

## 6. 不做的事

- 不改项目身份，不合并重复项目，不迁移现有数据。
- 不让 Hindsight 存工作流水，不改 Memory 召回。
- 不调用模型做摘要。
- 不在每轮注入，也不在每轮写 Checkpoint。

## 7. 验收（A、B）

1. 在 videoMake 结束一个 Codex 会话：`session_checkpoints` 新增一条 `SESSION_END` 记录，包含用户输入列表和 git 状态。
2. 直接关闭 Claude 桌面会话（收不到 SessionEnd），再在同一工作区开新会话：上一个会话被补写一条 `RECOVERED` 记录。
3. 新开 Claude 会话，第一句问"我刚才在做什么"：回复能说出上一个会话的最后几件事，不论上一个会话是 Claude 还是 Codex。
4. 同一工作区新开 Claude 会话（startup）：上下文里有接续块，包含"自那以后工作区的变化"；receipt 记为 `trigger: session_start`。
5. `--resume` 恢复会话、普通提问：都不注入，Memory 提示行为与现在一致。
6. 在 `_worktrees/<x>` 启动的会话能看到 `jasmine-core-v1` 主目录的记录，反之亦然。
7. 重复投递同一个 SessionEnd：返回重放，不新增记录。
8. `jasmine-core worklog --since 1d` 按时间列出当天的工作记录。
9. 接续块不超过预算，凭据形状的内容被遮掉。

## 8. 待定

- 意图模式的误触发和漏触发：上线后用 receipt 抽查 20 条再调整。
- 主会话是否优先选同一 agent：初版不区分，按时间取最近的。
- 工作记录保留多久：初版不过期，它和事件一样是只追加的历史。
- Codex、Grok 会话里能否注入：另开 ADR。
- Task 治理的具体规则：另开 ADR（第 5 节第 1 条）。
