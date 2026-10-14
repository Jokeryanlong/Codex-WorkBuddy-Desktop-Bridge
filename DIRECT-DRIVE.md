# 0.4.3 原生 SDK 直驱与增量进度

参考代码：[Firefox Reverse AgentSession](https://github.com/WhiteNightShadow/firefox-reverse/blob/efefea6672259ae2223215a705c8653b283c2338/additions/browser/components/agent-sidebar/modules/AgentSession.sys.mjs) 与 [ToolRouter](https://github.com/WhiteNightShadow/firefox-reverse/blob/efefea6672259ae2223215a705c8653b283c2338/additions/browser/components/agent-sidebar/modules/ToolRouter.sys.mjs)。仅借鉴能力发现、确定性派发、有界结果和运行中互斥的设计，没有复制其工具实现。

Firefox Reverse 的 `listTools / callTool` 通过同一个工具路由器直接执行内置工具，绕过内部模型。WorkBuddy 当前已验证的公开 SDK 提供原生会话控制，但尚未发现等价的通用内置工具执行入口。两者能力不能等同。

## 已实现

- `workbuddy_capabilities()`：发现已经验证的能力及边界，不发送模型请求。
- `workbuddy_session(session_id)`：直接查询原生会话状态、模式、权限、最新请求及工具名称/状态。不发送模型请求，不切换会话，不改变模式。
- `workbuddy_progress(task_id, cursor=0, limit=50)`：读取运行中公开回答增量、工具状态和原生状态。每轮把 `next_cursor` 作为下一次的 `cursor`，避免重复输出历史。
- SDK 返回的 `reasoning`、用户提示词、工具参数、工具原始结果不进入进度接口。
- 桌面会话正在工作、规划或等待用户响应时，拒绝另一轮续接，不抢占正在运行的会话。
- Ask 模式、原生对话窗口及上下文续接保留。

## 领航顺序

1. 先用 `workbuddy_status` 与 `workbuddy_capabilities` 判断连接及已验证能力。
2. 状态检查直接调用 `workbuddy_session`，不让内部模型代为查询。
3. 需要分析时，`workbuddy_start` 只提出一个有界问题。
4. 用 `workbuddy_progress` 按游标查看进度；用短 `workbuddy_wait` 等待。短等待结束不等于任务已结束，不能因此重复提交。
5. `finished=true` 后查看任务状态。成功时读取 `workbuddy_result`，核验答案，再用同一 `session_id` 发下一步。
6. `awaiting_user=true` 时在 WorkBuddy 桌面处理等待的响应，不自动批准。任务失败时读取 `error`，不要继续发送下一步。

## 返回与限制

进度缓存最多保存 256 条事件；单条回答片段最多 4096 字符，单页约束在 12000 字符预算内。旧事件已被淘汰时返回 `history_gap=true`，不会伪装成完整历史；最终答案仍从 `workbuddy_result` 获取。

回答被模型重写时返回 `answer_replace`，调用者应替换已累积的回答；后续 `answer_delta` 继续追加。工具状态变化才产生事件，未变化的轮询不重复发送。

本版本仍通过本机 CDP 调用 WorkBuddy 原生 SDK，并轮询 SDK 请求快照。没有改造为 WorkBuddy 服务端事件推送，也没有开放任意 JavaScript 执行或内置 Shell 工具直调。该边界在 `workbuddy_capabilities` 中明确报告。

进度缓存在当前 MCP 进程内，MCP 重启后不恢复旧 task_id。原生会话仍可通过 session_id 直接查询。该功能不承诺提供模型完整内部推理。
