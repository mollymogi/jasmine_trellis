# ADR 0023: 本机 Memory 引擎显式启动

范围：本机运维操作，不是记忆内容写入，也不改变 Authority/Task State。用户明确要求在健康页一键启动；先在隔离实例验证，日常服务保持原样。

- `GET /v1/memory/engine` 使用 `memory:read`，返回可管理性、调用者能否启动、STOPPED/STARTING/RUNNING/FAILED/UNAVAILABLE、操作 ID、版本与稳定错误码。
- `POST /v1/memory/engine/start` 使用独立 `memory:engine:manage`，仅 human/system；agent 既不能领此 scope，也不能调用。请求体只能 `{}`，不接受查询参数。
- 启动命令、工作目录、环境和 loopback origin 均来自本机运维设置 `JASMINE_MEMORY_ENGINE_CONTROL_CONFIG`。HTTP 不能传命令、端口、URL、凭据或目录。代理通过独立开关 `P6_ENABLE_MEMORY_ENGINE_CONTROL` 开放，默认不转发。POST 拒绝跨源 Origin。
- 同一 Core DB 的控制器跨 HTTP 请求共享；并发与重复启动复用当前操作。启动在后台执行，GET 只读操作状态，轮询不重放 POST。错误码不包含命令、配置路径、原始日志或凭据。
- 只管理自己启动的进程组；预设端口被占用时拒绝接管。退出/超时清理自有子进程。Core 正常关闭时释放控制器；启用管理配置的 CLI Core 收到 SIGTERM 会执行关闭路径。
- readiness 使用真实 Hindsight /version 与适配器版本检查，并在独立连接、无 Truth 写事务的后台线程记录引擎健康。服务可达仅表示连接就绪，不更新 last_success_at，不声称 retain/recall 已通过。现有健康 GET 仍不进行网络探测。
- 从本机受信配置接入已启动的 transport，不允许请求选择引擎地址。启动不启用 Memory worker 或 Context 自动注入，不补造来源/回执。
- 不改 SQLite schema；现有只读 key 不提升权限。使用者需以含新 scope 的本机运维 key 登录。引擎凭据仅由服务端启动器读取既有 Keychain，不返回浏览器，不修改 Keychain 权限。

`managed-hindsight.py` 支持独立 HOME、缓存与 pg0 数据库端口。隔离演练仅复制模型/可执行缓存，不复制或连接日常 PostgreSQL 数据。
