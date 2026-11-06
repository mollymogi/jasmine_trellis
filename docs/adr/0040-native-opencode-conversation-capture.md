# ADR 0040：OpenCode 原生对话采集、工作记录与接续

状态：已实现（组件测试）；尚未在真实 OpenCode 会话里验收。日期：2026-11-06。Schema：不变（17）。
相关：ADR 0026（Codex 日常采集）、0027（Grok）、0028（各 Agent 接入）、0029（对话 → 提炼）、0036（Memory 提示）、0039（工作记录与接续）。

## 1. 背景

OpenCode 1.18 已在看板里被检测到，但一直标为"已安装，等待采集"。它没有 Claude / Codex / Grok 那样的命令 hook，
扩展点是在 OpenCode 服务进程里运行的 JS 插件（`~/.config/opencode/plugins/*.js`，`@opencode-ai/plugin` 的
`Hooks` 接口）。其中可用的是：

| 插件钩子 | 何时 | 能否给模型加内容 |
| --- | --- | --- |
| `event` → `session.created` | 新会话创建（子会话带 `parentID`） | 否 |
| `chat.message` | 用户消息保存前，`output.parts` 可修改 | **能**：追加的 part 会随消息保存并发给模型 |
| `event` → `message.updated` / `session.idle` | 助手回复完成 / 本轮结束 | 否 |
| `experimental.session.compacting` | 压缩开始前 | 只能改压缩提示词，不用 |
| `command.execute.before` | 斜杠命令展开前 | 否 |

## 2. 决定

### 2.1 插件只转发 id，Python 负责核对和记录

安装器写一个自有文件 `~/.config/opencode/plugins/jasmine-trellis.js`，里面固定好解释器、launcher、设备绑定、
receipt 路径和 OpenCode 数据目录。插件每次把一个 JSON 对象写给
`python -I -B <launcher> --device-binding … --receipts … --opencode-data …`（即 `capture/opencode_session.py`），
一次只跑一个进程，按事件顺序排队；超时（提问 4 秒，其他 8 秒）或失败就当没发生，OpenCode 不受影响。
子进程环境只有 `PATH=/usr/bin:/bin` 和 `HOME`。

| 插件发送 | 记录为 |
| --- | --- |
| `session.created`（仅无 `parentID` 的主会话） | `session.started` |
| `chat.message`：用户自己的文本 part（去掉 synthetic / ignored） | `user.prompt`（真人 key） |
| `session.idle`：本会话最近一条已完成、非压缩摘要的助手消息 id | `assistant.message`，正文由 Python 从数据库读 |
| `experimental.session.compacting` | `PRE_COMPACT` 会话 Checkpoint，不产生 Event |

斜杠命令展开出的消息不是用户原话，不记录。子会话（subagent）的一切事件都不记录。没有会话结束事件；
未收尾的会话由下一个同工作区会话启动时补写（`RECOVERED`，ADR 0039 §3）。

### 2.2 来源核对用 OpenCode 自己的数据库

Grok 用 `summary.json`、Claude 用 transcript 核对会话来源；OpenCode 对应的是 `opencode.db`（SQLite，WAL）。
Python 以只读方式打开（`mode=ro`），要求文件属于当前用户、不可被他人写、不是符号链接，然后：

- 会话必须存在，且 `parent_id` 为空。工作区目录取自这一行的 `directory`，不信任插件传来的路径。
- 提问：消息 id 不能已属于别的会话（`chat.message` 在保存前触发，所以本会话里还没有它）。
- 回复：消息必须是本会话的 `assistant` 消息、已完成、不是压缩摘要；正文是它的文本 part 按顺序拼接，
  不含工具输出和 synthetic part。

只读取会话元数据和这一条回复，不扫描历史，不补录。项目、会话、事件的身份与 Grok 相同（工作区根 + host），
重放幂等。来源系统 `opencode-native-conversation-v1`，并加入对话提炼的来源列表（ADR 0029）。

### 2.3 接续块和 Memory 提示：附在用户消息上

OpenCode 能在 `chat.message` 时改消息，所以不需要像 Grok 那样延后（ADR 0039 §4.6）：

- Python 在 `chat.message` 的回复里给出 `{"context": text|null}`，插件把非空文本作为一个
  `synthetic: true` 的文本 part 追加到这条用户消息上（part id 按 OpenCode 的格式生成，排在原有 part 之后）。
  synthetic part 在界面上不显示为用户输入，但会发给模型。
- 会话启动：插件记下本进程里见到创建的主会话，它的第一句提问带 `starting: true`，按会话启动触发接续块
  （除非这句本身点名了范围或 agent）。之后的提问按 ADR 0039 §4.1 / §4.5 的提示词规则。
- 每次提问照常请求 Memory 提示（ADR 0036），两段文字合并。
- 开关与 Memory 提示共用设备绑定里的 `memory_hint`；插件文件本身不随开关变化。

### 2.4 安装与看板

- `install_opencode`：只管理自己的文件（以标记行识别），拒绝覆盖别人的同名文件和符号链接配置，保留其他插件
  （例如 herdr 的），写之前留备份；`--disable` 只删自己的文件，并关闭 Memory 提示。`--memory-hint enable`
  要求插件已安装。OpenCode 下次启动时加载插件，不改它的任何其他配置。
- `prepare-agent-capture.py` 为 OpenCode 建立设备绑定、两把采集 key 和 `memory:recall` key，配置目录是
  `~/.config/opencode`。
- 看板的 Agent 接入对 OpenCode 开放一键安装 / 停用和 Memory 提示开关。"已配置"要求插件文件带标记行，
  且其中固定的解释器、launcher、设备绑定、receipt 与运维配置一致。
- 会话树、对话来源标签、`worklog --agent opencode`、提示词点名（"opencode 刚才在做什么"）都识别 OpenCode。

## 3. 不做的事

- 不读 OpenCode 的历史会话，不补录安装前的对话。
- 不用插件做 Guard / 工具拦截（Host Hook 协议不覆盖 OpenCode）。
- OpenCode 不作为对话提炼的客户端（ADR 0032 维持现状）。

## 4. 验收

组件测试（`tests/test_opencode_session_capture.py`）：真实 Core 上的提问 / 回复原文、重放幂等、子会话 / 未知会话 /
未完成回复 / 他人消息 id / 可被他人写的数据库都不记录、压缩写 Checkpoint、新会话第一句带接续块、关闭开关后不加内容、
安装器的所有权与停用；并用 Node 实际运行生成的插件，确认事件转发顺序、子会话与斜杠命令被跳过、上下文以 synthetic part
附加。

真实验收尚未进行：需要在真实 OpenCode 1.18 会话里确认插件加载、`chat.message` 追加的 part 进入模型、
`message.updated` / `session.idle` 的时序，以及看板显示。
