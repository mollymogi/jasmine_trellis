# 本机引擎一键启动

候选 `cfc30cf6fadfc7f0c8f42efcb2ef1cecf78f84f7`。
结果：**PASS_MEMORY_ENGINE_START_SCOPED_AUTHOR_VERIFICATION**。

健康页卡片增加显式启动。服务端只执行本机预设启动配置，独立 scope `memory:engine:manage`；现有只读 key 仍返回 403，agent 不可领取或使用此权限。界面分别呈现未启动、启动中、已连接、失败/重试；轮询只做 GET，不重放 POST。Core 健康不会把 readiness 当作记忆操作成功。

真实 Chrome 在完整 Dashboard 点击启动，Hindsight **0.10.2** 启动并初始化独立 PostgreSQL，监听 **53201 / 53202**。Core 回读 `configured=true, reachable=true, status=idle, last_success_at=null`，页面刷新为“引擎已配置，尚无成功操作记录”，显示“引擎已连接”。重复启动复用同一 operation_id。真实启动浏览器回执 **9 项通过**。

最终页面桌面/390px、亮暗主题、对比度、详情与来源导航另 **16 项通过**。前端 **573** 项通过；Memory 回归 **525** 项（1 skip）通过，启动控制最终 **10** 项通过（套件有重叠，包含崩溃健康回读与关闭清理）；API 回归 **29** 项通过，生产构建通过。最初新增代理测试的测试目录默认值不适用于 Vitest，改为显式 server 目录后通过；失败日志保留原样。

八张 Truth/Memory/jobs 表摘要不变，日常 19 个配置/脚本/plist 与四个监听 PID 不变。只启动私有隔离实例：独立 HOME、模型缓存副本、数据库实例；复用既有运行库与 Keychain 凭据读取，未修改 Keychain 权限或浏览器暴露 LLM 凭据。没有启动 Core Memory writer，没有启动自动注入，也没有重跑 P4 收益实验。

原有 Memory 条目是明确标注的 synthetic fixture，不因引擎启动而变成真实 retain 回执。此结论不包含 retain/recall、自动采集/注入、完整 P6/P4/V1 或独立签署。

原始文件校验见 [manifest](ENGINE-START-MANIFEST.json)。主要证据位于：

- `/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/engine-control-real-01/checks.json`
- `/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/engine-control-ui-final/checks.json`
- `/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/isolated-dashboard/engine-control-boundaries.json`
- `/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/engine-control-web-passed.log`
- `/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/engine-control-memory-regression.log`
- `/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/engine-control-backend-tests-final.log`
- `/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/engine-control-api-regression.log`

本地配置和密钥不入库；源代码已提交本地隔离分支，未推送、合并或部署日常服务。隔离服务继续运行供用户查看。
