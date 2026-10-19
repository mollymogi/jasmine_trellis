# Memory 页面阅读层级优化

候选：`4193411b6fa1012c8b5ee9f19f991b6966641d1d`；基线 `a9178a66cb96fd40743435a673f1be84c70cfcc2`。
结果：**PASS_COMPACT_UI_SCOPED_AUTHOR_VERIFICATION**。未独立签署、未推送/合并/部署。

默认先显示一句话结论，例如“这条记忆中的决策已被新规则替代，仅供历史参考”。
警告保持可见；状态码、判定原因、版本、来源 Event、原文分别保留在“查看对照详情”中。
记忆内容/记录、管理操作、其他条目入口与读取时间默认折叠。
召回页先给出过滤概况，再按需打开原因与记录；页面顶部的长功能说明也改为可展开。

使用原生 details/summary，可鼠标和键盘操作。展开不提交写入，原有管理命令仍需显式提交，
Core 接口、权限、过滤和 Truth 逻辑没有修改。切换条目会重置该条目相关的展开状态。

验证：前端全量560项通过，生产构建通过；真实Core/Chrome演练20项链路检查通过，
浏览器live22项、readonly12项通过，包含默认关闭、技术信息初始不可见、展开后的来源跳转、
展开不发写请求、刷新后状态更新、低权限对照和原业务内容摘要不变。
日常19个配置/脚本/plist hash、4个监听PID、schema12与健康状态保持不变；演练服务与浏览器已停止。

验证数据仍为明确标注的synthetic fixture，Memory引擎为既有FakeTransport测试替身，
不把这轮界面验证升级为真实Hindsight/模型/P4收益或完整P6验收。
两处最初单测假设“jsdom会按原生details关闭状态过滤role查询”不成立；已改为核对open状态，
真实可见性由Chrome的innerText检查验证。失败日志保留，没有改写旧验收。

本机预览：

- [默认视图](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/compact-gate-01/superseded-memory-warning.png)
- [展开详情](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/compact-gate-01/superseded-memory-expanded.png)
- [真实浏览器回执](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/compact-gate-01/browser-live.json)

复现：先npm ci与npm run build，再运行既有`scripts/p6/memory_authority_gate.py --out <新私有目录>`。
本轮不重复运行后端517项，因为后端源码和接口均未改变；此前结果保留原提交绑定。
