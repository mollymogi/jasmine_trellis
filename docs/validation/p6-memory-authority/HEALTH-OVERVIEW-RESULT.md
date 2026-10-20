# Memory 健康页概览改版

候选 `d7c2314d5bee7534168d237716d2174ee6201ca2`。用户实际查看的是 `#/memory/health`，此前详情页的更新未覆盖此页；本次直接替换该健康页的字段堆叠展示。

首屏呈现 Core 返回的实际状态、一句话说明、保留中的记忆/待处理任务/失败任务；主区提供记忆条目、队列与召回记录入口。原有健康字段、完整计数、功能状态与非权威声明保留在运行详情 Sheet，记忆非权威的说明也保留在首屏。Memory 主导航进入该概览。

`unconfigured` 不等于运行故障；`idle` 不等于已成功；未知状态或缺失计数不补成正常或零。只读查看不启动引擎、不探测外部服务、不修改配置。Core 原有接口、数据和写入语义保持不变。

验证：前端 **564** 项通过，生产构建通过。真实 Chrome **16** 项检查通过：直接在健康路由看到新概览、1440/390px 亮暗主题无横向溢出及文字对比度 ≥4.5:1、技术字段默认隐藏、完整详情、Escape 焦点恢复、进入记忆详情与从主导航返回概览；所有浏览请求都是 GET。

证据目录：`/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/evidence/dashboard-health-overview`。测试日志：`/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/health-dashboard-final-tests.log`；构建日志：`/Users/mercuryliu/Documents/JasmineCoreWork/p6-memory-authority-20261018-201955/logs/health-dashboard-final-build.log`。下表固定该轮浏览器回执和截图。

| 文件 | SHA256 |
| --- | --- |
| checks.json | `58877ddd4c4ec9a1029d4efd6b1997bebb07aa08a9a40d7a418756399eaecbda` |
| desktop-dark.png | `81ac3d7a0935e08134aa4534cfb3b656cf92cd8945cfa103a61446959051e5fc` |
| desktop-light.png | `35a7c7bf269395ae649f796a6d3b526fcf5e77571fe29889889d91586b1f6107` |
| mobile-dark.png | `dcfb4dfe2000bf7ec38fccc32275e018fd410f0f0b50f549d854ed491d32bde0` |
| mobile-light.png | `3e1ba73e4b529840af8f16bd0135accb324c02f03400384a5db93cc9fe6a8c20` |

范围：作者在隔离完整 Dashboard/Core 中使用明确标注的模拟记录验证；未连接真实 Hindsight、未签署完整 P6/V1 验收。日常服务未改动，本地隔离 Dashboard 保持运行供用户查看。修改未推送/合并/部署到日常服务。
