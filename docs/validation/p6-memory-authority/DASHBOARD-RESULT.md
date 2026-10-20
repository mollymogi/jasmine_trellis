# Memory 详情看板排版验证

候选：`893646085faccf251bcd5d64f0645ffae900a0e4`。结果：**PASS_DASHBOARD_UI_SCOPED_AUTHOR_VERIFICATION**。
此记录替代旧折叠布局作为当前界面方案，旧 COMPACT-RESULT 和失败回执保留原样。

## 页面变化

- 首屏一句话结论，异常和未知状态直接可见；3 个指标独立成卡片。
- 主区呈现历史来源与当前规则对照，辅助栏放来源概况与操作；完整来源/元数据与管理表单用 Sheet/Tabs 打开。
- 引入 mogi-ui DetailLayout 候选源码，仅替换导入别名；上游 [PR #24](https://github.com/mollymogi/mogi-ui/pull/24) 已创建，组件来源固定于 `5afbc02dd0544837e6daa6417010591e40130893`。该 PR 尚未合并或发布。
- 当前 Core/API、权限、记忆过滤和写入命令语义保持原有逻辑。来源链接必须实际完成路由跳转，打开详情不发送写入。

## 验证

前端全量 **560** 项通过，生产构建通过。真实 Core/Chrome 隔离演练 **20** 项检查通过，
浏览器 live **27** 项、readonly **12** 项通过。包括替代/退役/未关联/无权限状态、
刷新、历史版本、来源跳转及重载、脚本字符串不执行、召回过滤、只读 Truth 摘要不变。
1440px 主区/辅助栏约 2:1，390px 无横向溢出；亮/暗主题文字与按钮采样最低分别 5.37:1、5.99:1。
独立组件库演示另通过 17 项真实 Chrome 检查和 typecheck/registry/build。

证据：`/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/dashboard-gate-06`，校验绑定见 [manifest](dashboard-evidence-manifest.json)。
前端测试日志：`/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/dashboard-web-final.log`，构建日志：`/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/dashboard-build-wrap.log`。
早期移动端溢出失败，以及浏览器在 Sheet 动画结束前点击/仅凭既有文本判导航成功的失效回执均保留。
当前脚本等待动画结束、要求目标路由与目标内容同时就绪，截图等待主题过渡结束；没有放宽溢出或对比度门槛。

## 边界

Memory 引擎使用 FakeTransport，数据明确标注 SYNTHETIC。该验证不等于真实 Hindsight/模型收益、独立签署或整个 P6/V1 验收。
日常 19 个配置/脚本/plist hash、4 个监听 PID、schema 12 与健康状态保持不变。
所有自建演练服务和浏览器均已停止；未部署。仅上游组件库特性分支已推送，本地 Trellis 分支保留供后续审核。

## 本机截图

- [亮色看板](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/dashboard-gate-06/dashboard-light.png)
- [暗色看板](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/dashboard-gate-06/dashboard-dark.png)
- [手机](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/dashboard-gate-06/dashboard-mobile.png)
- [浏览器 live 回执](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/dashboard-gate-06/browser-live.json)
- [浏览器 readonly 回执](/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/dashboard-gate-06/browser-readonly.json)
