# WorkBuddy 桌面原生对话 MCP 分享版 0.4.2

这是源码安装包。Codex 将任务发送到 WorkBuddy 桌面原生对话，你能在 WorkBuddy 自带的对话窗口看到执行过程。它不是独立的 CodeBuddy CLI。

## 接收方准备

- Windows；安装并登录 WorkBuddy 桌面端，当前已实测 WorkBuddy 5.8.2。
- 安装 Codex 桌面端及 Windows Python 3.10 或更新版本，Python 命令可用。
- 首次安装需要联网下载 Python 依赖。本 ZIP 不含 WorkBuddy、Python、Codex 安装包。

## 安装

1. 解压到长期保留的位置，例如 `D:\Tools\WorkBuddy-Desktop-Bridge-0.4.2`。安装后不要移动或删除目录。
2. 先打开已登录的 WorkBuddy。在解压目录打开 PowerShell，运行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   如果不能自动找到 WorkBuddy，可指定实际安装位置：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -WorkBuddyPath "D:\Apps\WorkBuddy\WorkBuddy.exe"
   ```

   Python 不在 PATH 时，加 `-PythonCommand "C:\实际位置\python.exe"`。

3. 完整退出 WorkBuddy，再使用安装器创建的桌面快捷方式 **WorkBuddy - Codex Native Chat** 打开；然后重启 Codex。

安装器创建本目录 `.venv`，安装依赖，为当前用户 Codex 添加 `workbuddy_worker`，保留其他 MCP 配置，并将原配置备份到本目录 `.install-backups`。默认使用用户 `.codex\config.toml`；设置了 `CODEX_HOME` 时使用该目录。

## 验收

运行 `doctor.cmd`，确认桌面发现和原生 SDK 检查通过。在重启后的 Codex 中要求使用 `workbuddy_worker` 发送“连接测试，请回复 WB_OK”。确认 WorkBuddy 原生聊天窗口出现该任务，且 Codex 收到同一回复。只看到进程启动不等于通过验收。

必须使用新快捷方式启动 WorkBuddy，它启用 `127.0.0.1:18794` 的本机自动化入口。启动器检测到普通模式的 WorkBuddy 时提示退出重开，不强制结束任务。端口占用时，安装命令可加 `-Port 18795` 指定其他空闲端口。

## 包内文件

- `workbuddy_bridge/`：MCP、原生桌面对话驱动、启动器及测试。
- `setup.ps1`、`install_codex.py`：依赖安装、生成接收方配置和快捷方式。
- `start.cmd`、`doctor.cmd`：运行和诊断入口。
- `pyproject.toml`、`requirements.txt`：依赖与包元数据。
- `README.md`、`LICENSE`、`docs/`：文档和许可证。
- `.codex/skills/workbuddy-agent-routing/`：可选路由 Skill，供使用者自行选用。

不包含分享者的 `.venv`、Codex 全局配置、WorkBuddy 账号目录、登录凭据、任务记录、EXE 样本或分析结果。接收方使用自己的账号登录。

## 回滚与卸载

安装器每次改配置前保存 `BASELINE.toml`、`MODIFIED.toml`、`DIFF.patch`、`VERIFICATION.txt` 和 `ROLLBACK.ps1`。需要撤销时，在最新备份目录运行 `ROLLBACK.ps1`。安装后若又修改了配置，回滚会拒绝覆盖；此时根据 `BASELINE.toml` 手动恢复或删除 `workbuddy_worker` 配置段。删除生成的快捷方式和本安装目录即可卸载源码与环境。回滚后重启 Codex。

该版本使用 WorkBuddy 内部桌面 SDK，升级 WorkBuddy 后需重新验证兼容性；目前没有证明兼容所有版本。

## 0.4.2 问答模式

新建、续接和每轮发送前均强制设置并核验 Ask 问答模式与 default 权限，避免默认进入 craft 全自动执行。每次只提出一个分析问题，收到答案后再进入下一步。Ask 不代表所有请求都会立即完成，执行时限仍然生效。
