# Trellis（Jasmine Core）能力手册

整理日期：2026-11-06，对应 `main` 提交 `9630f39`（schema 17）。

本手册汇总当前代码里已有的功能和使用方法。每项能力的完整契约以对应 ADR 为准。
“代码里有”不等于“日常部署已开启”：本机实际运行的版本、开关和验证结果，看私有部署目录里的
`RESULT.md` / `RELEASE-RESULT.md`。

> 命名：产品已改名 **Trellis**，仓库是 `jasmine_trellis`。Python 包 `jasmine_core`、命令
> `jasmine-core`、`JASMINE_CORE_*` 环境变量和磁盘路径暂时保留原名。

---

## 1. 它是什么

Trellis 是一个运行在本机的“Agent 工作底座”。它把 Claude Code、Codex、Grok 等编码 Agent 的
对话和工具调用记录下来，做成可追溯的事实，在这之上提供：

- **真相层**：项目 / 任务 / 会话、不可变事件、规则（Authority）、任务状态、证据。
- **守卫层**：工具调用前按规则检查（允许 / 确认 / 拒绝）。
- **连续性**：Checkpoint、Resume、Context Pack、新会话接续。
- **记忆层**：从对话中提炼决定、纠正、约定，写入 Hindsight，按需召回给 Agent。
- **人用看板**：浏览对话、记忆、规则、审核、验收记录。

### 本机组件

| 组件 | 地址 | 作用 |
| --- | --- | --- |
| Core API | `http://127.0.0.1:18787/v1/...` | 全部数据与业务逻辑，只监听本机，Bearer key 鉴权 |
| 看板 Dashboard | `http://127.0.0.1:15180/` | 人用界面，登录时粘贴本机凭据里的 token |
| Hindsight | `127.0.0.1:18084`（PostgreSQL 15434） | 长期记忆引擎（retain / recall / rerank） |
| Memory worker | 后台进程 | 唯一写入 Hindsight 的进程，处理持久化 Memory Job |
| 对话处理 worker | 后台进程 | 把新采集的用户提问送给 Interpreter（提炼器） |

四个用户级 LaunchAgent 负责开机自启和异常重启。配置、日志、凭据都在私有目录
`~/Library/Application Support/JasmineCoreV1/local-item6/`。

---

## 2. 功能清单

| # | 能力 | 一句话 | 入口 | 依据 |
| --- | --- | --- | --- | --- |
| 1 | 事件存储 | 不可变、幂等的原始事件；原文保留并做敏感信息遮盖 | `POST/GET /v1/events` | ADR 0002–0004、P0-02 |
| 2 | 项目 / 任务 / 会话 | 统一的对象模型，公共 ID（`prj_`、`tsk_`、`ses_`…） | `/v1/projects` `/v1/tasks` `/v1/sessions` | ADR 0001 |
| 3 | 对话自动采集 | Claude Code、Codex、Grok 的日常对话自动进 Core，按工作区自动建项目 | Agent 用户级 hook | ADR 0026–0028 |
| 4 | 对话浏览 | Agent → 项目 → 会话树、时间线、搜索、原文阅读 | 看板「对话」 | local-agent-discovery |
| 5 | Agent 接入 | 检测本机已安装的 Agent，一键安装 / 停用采集 hook | 看板「对话 → Agent 接入」 | ADR 0028 |
| 6 | 对话提炼（Interpreter） | 用本地 Codex / Grok 客户端把用户的话提炼成任务、规则、决定、纠正等候选 | 看板「对话提炼」 | ADR 0008、0029、0031、0032 |
| 7 | 解析与审核（Resolver / Review） | 候选按策略自动应用或进入人工审核 | 看板「待审核」、`/v1/reviews` | ADR 0009 |
| 8 | 规则目录（Authority） | 全局 / 项目 / 任务规则的版本化管理：提议、批准、替换、退役 | 看板「规则」、`/v1/rules` | ADR 0005、0033 |
| 9 | 守卫（Guard） | 工具调用前按有效规则返回 allow / confirm / deny | `POST /v1/guard/check`、Host Hook `tool.before` | ADR 0005、0035 |
| 10 | Host Hook 协议 | Agent 中立的事件协议：提问、工具前后、注入、Checkpoint | `jasmine-core hook` / `hooks` | ADR 0017–0021 |
| 11 | 任务状态与验收 | Task / Step 状态机、验收标准、fail-closed 验收 | `/v1/tasks/{id}/transition` 等 | ADR 0006 |
| 12 | 证据（Evidence） | 工具结果按可信级别入库；工作区指纹比对；人工确认只产生 INFO | `/v1/tool-results` `/v1/evidence/*` | ADR 0007、0015 |
| 13 | 任务 Checkpoint / Resume | 任务级快照与恢复回执 | `/v1/checkpoints` `/v1/tasks/{id}/resume` | ADR 0011 |
| 14 | Context Pack | 按预算精确构建注入给 Agent 的上下文（规则、任务状态、记忆） | `/v1/context/build` | ADR 0012、0025 |
| 15 | 长期记忆 | 对话中的决定、纠正、约定直接成为带可信标签的记忆条目，写入 Hindsight | 看板「记忆」、`/v1/memory/*` | ADR 0014、0034、0037 |
| 16 | 记忆自动提示 | Agent 收到提问时自动召回相关记忆，作为非权威提示注入 | Claude / Codex 的提示 hook | ADR 0036 |
| 17 | 记忆引擎启动 | 在健康页一键启动本机 Hindsight | 看板「记忆 → 健康」 | ADR 0023 |
| 18 | 工作记录（worklog） | 每个会话做了什么，按时间从新到旧 | `jasmine-core worklog`、`GET /v1/session-checkpoints` | ADR 0039 |
| 19 | 会话 Checkpoint | 压缩前、会话结束、恢复时自动记一份会话快照 | Claude / Codex hook | ADR 0039 |
| 20 | 新会话接续 | 新会话开始或问“我刚才在做什么”时，注入最近会话的接续块 | Claude / Codex hook、`POST /v1/continuity/recent` | ADR 0039 |
| 21 | 长输出摘要（digest） | 确定性地把长日志 / 文档 / JSON 压成摘要，原文落盘 | `jasmine-core digest` | ADR 0038 |
| 22 | 验收记录 | 只读展示已有的验证运行和原始 verdict | 看板「验收记录」、`/v1/validation/*` | ADR 0016 |
| 23 | 审计与鉴权 | 按 scope 发 key、吊销、追加式审计日志 | `/v1/auth/keys` `/v1/audit` | ADR 0004 |

---

## 3. 各 Agent 支持情况

| 能力 | Claude Code | Codex | Grok Build | OpenCode |
| --- | --- | --- | --- | --- |
| 对话采集（提问 + 回复） | ✅ | ✅ | ✅ | ❌ 仅检测到安装 |
| 看板显示会话 | ✅ | ✅ | ✅ | 显示“已安装，等待采集” |
| 对话提炼的客户端 | 未启用 | ✅ | ✅ | 未启用 |
| 记忆自动提示 | ✅ | ✅ | ❌ | ❌ |
| 会话 Checkpoint | ✅（PreCompact、SessionEnd、恢复） | ✅（SessionEnd、恢复） | ❌ | ❌ |
| 新会话接续块 | ✅ | ✅ | ❌（会被别的 Agent 查到） | ❌ |
| Host Hook（Guard / 注入 / 证据） | ✅ | ✅ | ✅ | ❌ |

Grok 和 OpenCode 的接续、记忆提示接入正在开发中。

---

## 4. 用法

### 4.1 接入一个 Agent

**看板方式（推荐）**：看板「对话」页 → 「Agent 接入」→ 选 Agent → 一键安装 hook。安装器只管理自己
那几条 hook，保留你其他的配置并留备份；不会改 Agent 自己的信任记录。

**命令行方式**（运维用，路径换成你自己的 device binding 和回执文件）：

```bash
python -m jasmine_core.capture.install_claude --device-binding DEVICE.json --receipts RECEIPTS.jsonl \
  --claude-home ~/.claude --launcher LAUNCHER --confirm-user-scope --memory-hint enable
python -m jasmine_core.capture.install_codex  --device-binding DEVICE.json --receipts RECEIPTS.jsonl \
  --confirm-user-scope --memory-hint enable
python -m jasmine_core.capture.install_grok   --device-binding DEVICE.json --receipts RECEIPTS.jsonl \
  --grok-home ~/.grok --launcher LAUNCHER --confirm-user-scope
```

加 `--disable` 卸载。`--memory-hint enable` 同时开启记忆提示和新会话接续。

接入后**不需要**在项目里放标记文件：采集按工作区（最近的 `.git`，否则启动目录）自动建项目。git
worktree 在接续时视为同一仓库。

### 4.2 看对话

看板「对话」：左边是 Agent → 项目 → 会话树，也可切到按时间分组的时间线；支持按项目名 / 路径搜索。
只记录真人提问和 Agent 的正式回复；子 Agent、定时唤醒等非真人消息不算提问。

### 4.3 对话提炼、审核与规则

1. 新的用户提问由后台 worker 自动送给提炼器，产出候选（新任务、规则、决定、纠正、验收等，普通闲聊
   是 `NO_STRUCTURE`）。
2. 看板「对话提炼」查看每条提问的原文、提炼结果和处理状态；右上角 **提炼器设置** 选客户端（Codex /
   Grok）和模型，只对之后的新分析生效。
3. 需要人确认的进入「待审核」。项目 / 全局约定必须人工批准后才成为规则。
4. 「规则」页浏览所有规则，查看来源、版本、匹配条件和历史；高级命令表单在显式操作后面。

### 4.4 记忆

- **怎么长出来的**：对话里的决定（“就用 v3”）、纠正、约定会直接成为记忆条目，带可信标签
  （`AUTHORITY` 来自已批准规则，`OBSERVED` 来自对话观察）。引擎没提取出内容的记为 `NOT_RECALLABLE`。
- **怎么用到的**：开启记忆提示后，Agent 每次收到提问（会话第一轮除外），hook 用你的原话做语义召回，
  交叉编码器重排后把最相关的几条作为“非权威提示”注入。召回超时 1 秒，失败不阻塞对话。
- **看板「记忆」**：健康状态、Job 队列、记忆条目（可置顶、标记过时、删除）、召回 trace、引擎一键启动。

### 4.5 工作记录与新会话接续

**自动注入**：同一工作区新开 Claude / Codex 会话时，开头自动注入一段“接续块”，告诉新会话最近在做什么。
同一会话 30 分钟内只注入一次。

**用提示词问**：

| 你说 | 查询范围 |
| --- | --- |
| “我刚才在做什么”“上次做到哪了”“记不记得…”“最近在做什么”“进度怎么样” | 本工作区，所有 Agent |
| “**codex** 刚才在做什么”“**Claude** 上次做到哪了” | 本工作区，只看该 Agent |
| “**其他项目**最近在做什么”“**全局**上次在做什么” | 全部工作区，所有 Agent |
| “**所有项目**里 **codex** 最近在干什么” | 全部工作区，只看该 Agent |

- 表示全部范围的词：所有 / 全部 / 别的 / 其他 / 各个 + 项目 / 工作区 / 目录 / 地方，“全局”“跨项目”，
  英文 all projects、other projects、everywhere。
- Agent 名：claude / 克劳德、codex、grok，不区分大小写；一句里点名两个则不过滤。
- 超过 200 字、斜杠命令、粘贴内容不触发。
- 提示词选择范围和 Agent 的部分在 PR #38 之后的新 PR 里，合并部署前只有默认问法可用。

**接续块内容**（不调用模型，是原文节选）：最多 3 个会话，按最后活动时间排序，总长 ≤1500 字符。
第一个会话最详细：时间、Agent、状态、轮数，最近 5 条用户提问（每条 ≤160 字），最新 5 个任务标题
（之前由提炼器生成），助手最后回复（≤400 字），以及工作区自那以后的 git 变化。另外两个会话各一行。

**命令行查工作记录**：

```bash
jasmine-core worklog                       # 最近 7 天，所有工作区
jasmine-core worklog --workspace . --since 12h
jasmine-core worklog --agent codex --limit 20 --json
```

### 4.6 长输出摘要（digest）

不需要数据库和模型。超出预算的输入会被压成摘要（结论行、错误 / 警告去重、头尾），原文完整保存，
摘要里给出行号方便回查。

```bash
jasmine-core digest build.log design.md
jasmine-core digest --run -- ./scripts/run-tests.sh      # 运行命令并摘要输出，退出码透传
jasmine-core digest --budget 2000 --json report.json
```

原文默认存在 `~/.local/share/jasmine-core/digests/`（`--out-dir` 或 `JASMINE_CORE_DIGEST_DIR` 改位置，
`--no-spill` 不存）。

### 4.7 Host Hook：守卫、注入与证据

Host Hook 是另一条 Agent 接入路径，用于需要**规则守卫**的项目：

```bash
jasmine-core hooks install --agent claude-code --scope user --device-binding DEVICE.json --confirm-user-scope
jasmine-core hooks status  --agent codex --scope user
jasmine-core hook replay   --device-binding DEVICE.json     # 重发离线期间暂存的事件
```

- 设备级注册后，只有放了 `<项目>/.jasmine/host-binding.json` 的目录才生效，其他目录完全不受影响。
- `tool.before` 时按规则返回 allow / confirm / deny；Core 不可达时按 `on_unavailable` 策略（默认拒绝）。
- 工具结果按可信级别记为证据：`NATIVE_HOOK` > `PROXY_OBSERVED` > `AGENT_REPORTED`。
- 缺少命令 / 路径信息时，只有当某条有效规则真用到这个维度，才会要求确认。

### 4.8 任务、证据与验收

看板「总览」选项目 → 项目页建 Task → Task 的“写入”页加 Step、编辑验收标准、转换状态。
TEST / PASS 必须来自真实执行和可信采集，界面没有“伪造通过”的按钮。人工确认在 Task 专用页准备、看完材料、
勾选后提交，只产生 INFO 级证据。

### 4.9 运维

```bash
jasmine-core migrate          # 建库或升级（只向前，不降级）
jasmine-core schema           # 查看当前 schema 版本
jasmine-core serve --port 18787
jasmine-core memory health    # 记忆引擎可用性
jasmine-core memory jobs      # 持久化 Memory Job
jasmine-core memory scan      # 给遗漏的来源补建 Job
```

- 升级前用 SQLite backup API 备份数据库和 config/bin，先在副本上验证升级、恢复和旧版启动。
- 备份只覆盖 Core 的 SQLite；Hindsight 的 PostgreSQL 不在内，可从 Core 来源重新投影。

---

## 5. 当前边界

- OpenCode 只检测安装，没有采集；Grok 没有记忆提示和接续。
- 任务级 Checkpoint（ADR 0039 C 部分）还没做，需要先清理 Task。
- 长提问（引擎超过 500 token）召回会被引擎拒绝，提示静默跳过。
- 没有自动备份调度、多设备在线、完整 Scenario Runner、`manual-reapply`。
- 完整 V1（P0–P7）验收未完成；各阶段的验证范围见 `docs/*-results.md`。
