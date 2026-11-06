# ADR 0041：单用户本机的看板自动登录

状态：已实现（组件测试）。日期：2026-11-06。Schema：不变。
修改 P6-A01 的登录方式（token 只在内存中），不改变 Core 鉴权。

## 背景

看板每次打开或刷新都要粘贴 token（`config/credentials/browser-daily.json` 里的 `token`）。这台机器只有一个使用者，
用户要求不再输入 token。

## 决定

- 看板服务新增 `GET /_local/session`，只在运维设置了 `P6_LOCAL_SIGNIN_CREDENTIAL`（指向上述凭据文件）时开启，
  否则 404。日常部署由 `runtime.json` 的 `dashboard_local_signin: true` 打开，`local-service.py` 据此设置环境变量。
- 只回答看板自己的页面：`Host` 必须是本机回环地址加本服务端口（挡住 DNS rebinding），浏览器必须标记
  `Sec-Fetch-Site: same-origin`，有 `Origin` 时必须同源；只接受 GET；不发 CORS 头；`Cache-Control: no-store`。
- 凭据文件必须属于当前用户、权限 0600、不是符号链接，token 格式合法，否则 503。
- 页面加载时请求一次，拿到后先做一次需要鉴权的读取（`GET /v1/projects?limit=1`）再进入看板；失败就显示原来的
  token 输入框。token 仍然只在页面内存里，不写任何存储，不进 URL。
- “退出登录”后本次页面保持退出状态，登录页多一个“本机自动登录”按钮。
- 下游不变：Core 仍按 token 的 scope 鉴权；Agent 接入、提炼器设置等本机控制仍比对 token 哈希。

## 边界

- 同一系统用户的其他进程本来就能读凭据文件，没有新增暴露。
- 本机其他系统账户能访问回环端口，可以伪造这些请求头拿到 token。这个模式只适合一台机器一个使用者，所以默认关闭。
- 不提供无 token 访问 Core 的通道；自动登录只是替人粘贴。
