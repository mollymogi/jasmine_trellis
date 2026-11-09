# ADR 0042：Antigravity 原生对话采集、工作记录与接续

状态：已实现（组件测试 + 注入通道真机验证）；尚未在日常部署里验收。日期：2026-11-09。Schema：不变（17）。
相关：ADR 0027（Grok）、0036（Memory 提示）、0039（工作记录与接续）、0040（OpenCode）。

## 1. 背景

本机装了 Antigravity CLI（`agy` 1.5.3），对话数据在 `~/.gemini/antigravity-cli/`。它有生命周期 hook：
用户级的 `~/.gemini/config/hooks.json`（工作区级是 `.agents/hooks.json`），顶层每个键是一个**具名 hook**，
来自不同来源的具名 hook 合并执行。命令经 `sh -c` 执行，stdin 是 JSON（camelCase），stdout 也要求是 JSON。

| 事件 | 时机 | 能否给模型加内容 |
| --- | --- | --- |
| `SessionStart` | 新会话开始 | 能：`injectSteps` |
| `PreInvocation` | 每次调用模型前（`invocationNum` 从 0 起，每轮重新计数） | 能：`injectSteps` |
| `PostInvocation` | 每次模型调用后 | 能，不用 |
| `Stop` | 一轮结束（`terminationReason`） | 只能要求继续，不用 |
| `PreToolUse` / `PostToolUse` | 工具前后 | 不用 |

没有会话结束和压缩前的事件。payload 不带事件名，也不带时间戳。

2026-11-09 用临时工作区实测（`.agents/hooks.json` 记录 payload）：

- 每个 payload 都有 `conversationId`（UUID）、`workspacePaths`、`transcriptPath`、`lastUserInput`。
- `PreInvocation` 的 `invocationNum: 0` 是这一轮第一次调用模型，`initialNumSteps` 是此时的步数，用户输入是第
  `initialNumSteps - 1` 步。
- `Stop` 有 `executionNum` 和 `terminationReason`，正常结束是 `NO_TOOL_CALL`。
- **`PreToolUse` 返回 `{}`（没有 decision）会拦下工具**，所以这里绝不登记 PreToolUse。
- 真机验证注入：SessionStart 和第一次 PreInvocation 各注入一个暗号，模型原样复述了两个，说明两处
  `injectSteps` 的 `systemMessage` 都进入模型。

transcript（`brain/<conversation>/.system_generated/logs/transcript_full.jsonl`）每行一步：`step_index`、
`type`（`USER_INPUT` / `PLANNER_RESPONSE` / …）、`source`（用户输入是 `USER_EXPLICIT`）、`created_at`、`content`；
用户输入包在 `<USER_REQUEST>…</USER_REQUEST>` 里。

## 2. 决定

### 2.1 三个 handler，一个具名 hook

安装器在 `~/.gemini/config/hooks.json` 里只拥有 `jasmine-trellis` 这一个键，保留其他键，写前备份，拒绝符号链接和
非对象文件；`--disable` 只删这个键并关闭 Memory 提示。每个事件一条命令，带 `--event`：

| handler | 记录 | 注入 |
| --- | --- | --- |
| `SessionStart` | `session.started`，并补写同工作区未收尾的会话（`RECOVERED`） | 接续块（会话启动触发） |
| `PreInvocation`，只处理 `invocationNum == 0` | `user.prompt`（真人 key） | 问到最近工作时的接续块 + Memory 提示 |
| `Stop`，只处理 `NO_TOOL_CALL` | `assistant.message` | 无 |

`invocationNum` 不为 0 时立刻输出 `{}`，不访问 Core、不写 receipt（每次模型调用都会跑这个 hook）。
注入格式 `{"injectSteps": [{"systemMessage": {"systemMessage": text}}]}`；没有内容时输出 `{}`。Stop 从不要求继续。

### 2.2 来源核对用 Antigravity 自己的 transcript

- `transcriptPath` 必须正好是 `~/.gemini/<antigravity-cli|antigravity|antigravity-ide>/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl`，
  会话目录和文件属于当前用户、不能被他人写、不是符号链接；只读文件末尾 4 MiB。
- 提问：第 `initialNumSteps - 1` 步必须是 `USER_INPUT`、来源 `USER_EXPLICIT`，且 `<USER_REQUEST>` 里的文字与
  `lastUserInput` 一致。来自子 agent、定时任务等其他来源的输入不记录。事件时间取该步的 `created_at`，轮次取步号。
- 回复：最后一条 `USER_INPUT` 之后、状态 DONE、没有工具调用、有正文的最后一个 `PLANNER_RESPONSE`。
- 会话启动时 transcript 可能还不存在，只核对会话目录；时间记为 hook 观察时间。
- 工作区取 `workspacePaths[0]`；项目、会话、事件身份与 Grok / OpenCode 相同，重放幂等。来源系统
  `antigravity-native-conversation-v1`，加入对话提炼来源列表。

### 2.3 开关与接入

- 接续块和 Memory 提示与其他 Agent 共用设备绑定里的 `memory_hint`；hooks.json 不随开关变化。
- `prepare-agent-capture.py` 为 Antigravity 建立设备绑定、采集 key 和 `memory:recall` key，配置根是
  `~/.gemini/config`。看板 Agent 接入检测 `agy` / `Antigravity.app`，提供一键安装 / 停用和 Memory 提示开关；
  "已配置"要求 `jasmine-trellis` 未被 `enabled: false` 关掉，且三个事件各有一条与运维配置一致的命令。
- worklog、`--agent antigravity`、提示词点名（antigravity / 反重力 / agy）、会话树和来源标签都识别 Antigravity。

## 3. 不做的事

- 没有会话结束、压缩前 Checkpoint（Antigravity 没有对应事件），只靠下一次启动补写。
- 不登记 PreToolUse / PostToolUse，不做 Guard。
- 只在 CLI 上实测；Antigravity 桌面版（`antigravity/`）和 IDE（`antigravity-ide/`）共用同一 hook 机制和目录约定，
  代码接受它们的 transcript 位置，但未实测。

## 4. 验收

组件测试 `tests/test_antigravity_session_capture.py`（真实 Core）：原文记录与重放、工具调用中间回复被跳过、
非轮次边界和取消 / 出错的轮次不记录、非真人来源 / 文字不符 / 路径不对 / 可被他人写的 transcript 不记录、
新会话注入接续块且第一句不重复、点名 agent 的提问注入、关闭开关不注入、安装器所有权与停用。
看板 `web/tests/agentCaptureControl.test.ts` 覆盖安装状态读回。日常部署后的真实会话验收尚未进行。
