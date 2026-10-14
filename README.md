# Codex ↔ WorkBuddy Desktop Bridge

让 Codex 领航 WorkBuddy 桌面端：Codex 提出一个有界问题，WorkBuddy 在自己的原生聊天窗口显示分析过程和答案，Codex 核验证据后再决定下一步。

**当前版本：0.4.3。默认 Ask 问答模式，默认权限 `default`。**

本项目直接调用已登录 WorkBuddy 桌面的原生 conversation SDK。不需要单独登录 CodeBuddy CLI，不创建独立后台聊天面板，也不复制账号令牌。此仓库保留 [上游项目](https://github.com/gosick233-cloud/Codex-WorkBuddy-Desktop-Bridge) 的 MIT 许可证，并发布原生桌面对话及 Ask 模式改进。

## 安装条件

- Windows。
- 已安装并登录 WorkBuddy 桌面端；当前实测版本为 **5.8.2**。
- 已安装 Codex，以及 Windows Python **3.10 或更新版本**。
- 首次安装可以联网下载 Python 依赖。

## 快速安装

1. 从 [Releases](https://github.com/Jokeryanlong/Codex-WorkBuddy-Desktop-Bridge/releases/latest) 下载 `WorkBuddy-Desktop-Bridge-0.4.3-share.zip`，解压到长期保留的目录。也可以使用 Git：

   ```powershell
   git clone https://github.com/Jokeryanlong/Codex-WorkBuddy-Desktop-Bridge.git
   cd Codex-WorkBuddy-Desktop-Bridge
   ```

2. 先打开已登录的 WorkBuddy，在项目目录打开 PowerShell，运行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   找不到 WorkBuddy 时，指定自己的实际安装位置：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -WorkBuddyPath "D:\Apps\WorkBuddy\WorkBuddy.exe"
   ```

   Python 不在 PATH 时，可加 `-PythonCommand "C:\实际位置\python.exe"`。默认端口被占用时，可加 `-Port 18795`。

3. 完整退出 WorkBuddy，再使用安装器创建的桌面快捷方式 **WorkBuddy - Codex Native Chat** 打开。随后重启 Codex。

安装器在项目目录创建 `.venv`、安装依赖，并添加当前用户 Codex 的 `workbuddy_worker` 配置。其他 MCP 配置保留，原文件备份在 `.install-backups`。设置了 `CODEX_HOME` 时使用该目录，否则使用当前用户的 `.codex/config.toml`。安装后不要移动或删除项目目录。

详细步骤、卸载和回滚见 [中文安装说明](INSTALL.zh-CN.md)。

## 连接验收

在项目目录运行 `doctor.cmd`，检查 WorkBuddy 桌面发现和原生 SDK 是否就绪。

在 Codex 中要求使用 `workbuddy_worker` 发送：“连接测试，请只回复 WB_OK。”确认 WorkBuddy 自带的聊天窗口出现该对话，并且 Codex 收到同一回复。

`workbuddy_status` 应返回：

```text
server_version: 0.4.3
backend: workbuddy-desktop-native-conversation
desktop_realtime: true
conversation_mode: ask
permission_mode: default
```

`doctor` 只验证连接就绪，真实问答完成才证明模型服务可用。

## 一步一步领航

- 每轮只发送一个明确的分析问题，收到答案后核验并决定下一步。
- 调用 `workbuddy_start` 提交任务，用 `workbuddy_wait` 等待短时间，或用 `workbuddy_result` 分页读取答案。
- 后续问题带上上一轮的 `session_id` 作为 `resume_session_id`，继续同一个原生会话。
- 使用 `request_id` 可安全重试；同一个 ID 使用不同参数会返回冲突。
- 自由问答省略 `identity`；代码审查可使用 S1/S2/S3 并绑定 `review_target`。
- `workbuddy_cancel` 取消本次任务，桌面对话和历史保留。

新建、续接及每轮发送前都会设置并核验 `ask` / `default`。旧会话或用户手动切回 `craft` 时，下一轮提交前恢复 Ask。设置失败会报错，不继续提交。Ask 不保证所有问题立刻完成；执行时限仍然有效，复杂问题应拆成小步骤。

默认并发为 1、队列上限为 32。可通过 `WORKBUDDY_MAX_CONCURRENT` 和 `WORKBUDDY_MAX_QUEUED` 调整。

## 启动与兼容性

专用快捷方式给 WorkBuddy 增加 `127.0.0.1` 的 Electron 自动化入口，默认端口 18794。普通快捷方式启动的 WorkBuddy 不一定有该入口；程序开着不代表连接可用。启动器检测到普通模式时会提示完整退出后重开，不会强制停止现有任务。

本项目使用 WorkBuddy 内部桌面 SDK，WorkBuddy 升级后需要重新验证兼容性。SDK 不可用、桌面重启导致绑定丢失或模式设置失败时明确报错，不静默切换到独立 CLI Worker，不盲目重发任务。

## 开发验证

```powershell
python -m pip install -e .
python -m unittest discover -s workbuddy_bridge -p 'test_*.py' -v
```

Windows + WorkBuddy 5.8.2 的本地验证：82 项测试通过，新建和续接原生 Ask 会话完成，旧 `craft/fullAccess` 会话恢复为 `ask/default` 后完成回复。GitHub Actions 在 Windows 上检查 Python 3.10 和 3.13；依赖真实 WorkBuddy Node 的 SDK 合约测试在未安装桌面端的 CI 中跳过。CI 不代表真实桌面连接验证。

## 分发与许可证

公开源码和下载包不含开发者的虚拟环境、全局 Codex 配置、WorkBuddy 账号目录、登录凭据、任务日志、样本或分析输出。使用者必须登录自己的 WorkBuddy 账号。

[MIT License](LICENSE)。
## 0.4.3 SDK 直驱与增量进度

新增 workbuddy_capabilities、workbuddy_session、workbuddy_read_file 和 workbuddy_progress；状态查询直接调用 SDK，不触发模型。任务执行时按游标读取公开回答增量和工具状态，再决定下一步。具体协议和能力边界见 [DIRECT-DRIVE.md](DIRECT-DRIVE.md)。
