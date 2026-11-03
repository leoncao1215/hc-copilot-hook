# Copilot CLI → Herdr App 状态集成

让 **新版 Herdr App / herdr-core (`hc`)** 显示 Copilot CLI 的初始空闲、正在工作、
等待回答或授权、本轮结束。不是旧版 Herdr TUI 的 `herdr integration` 插件，
也不是 GitHub 或 Herdr 官方集成。

## 支持范围

实测环境：**macOS、zsh、Python 3、原生 `copilot` 可执行程序**；
Copilot CLI **1.0.105**、herdr-core **0.1.17**、Herdr App **0.1.12**。
仅使用 Python 标准库，不安装 pip/npm 依赖。

Python 安装器只允许 macOS。**Windows / Dev Box** 使用独立的
[Windows 安装器与 agent 部署手册](windows/README.md)，不复用 `/dev/tty` 实现。
Linux、其他 shell，以及由 Node 等启动器承载的 Copilot 安装方式尚未验证，
不能据此宣称支持。
集成要安装在 **实际运行 Copilot 和 hc 的机器**，而不是仅显示远程 UI 的机器。

原生 OSC 7501 支持可能随版本更新；安装前应重新检查官方支持。如果官方已提供完整支持，
优先使用官方方案，不要同时运行两套状态生产者。

## 工作方式

```text
用户的交互式 zsh → copilot 启动包装 → 初始 idle
                        ↓
                官方 Copilot 二进制（不替换）
                        ↓
            官方生命周期 / notification hooks
                        ↓
             当前控制终端 /dev/tty 的 OSC 7501
                        ↓
                 herdr-core → Herdr App
```

| 场景 | 上报 |
| --- | --- |
| `copilot` / `copilot --resume` 刚启动，还没有发消息 | zsh 包装上报 `idle` |
| 用户提交消息 | `working` |
| 实际弹出授权请求 | `blocked`，`kind=permission` |
| 实际弹出问题表单 | `blocked`，`kind=question` |
| 主 agent 正常结束本轮 | `done` |
| 不可恢复的错误事件 | `error`，取决于 CLI 是否发出该事件 |
| 会话或包装中的 CLI 退出 | `clear` |

**为什么还有启动包装？** 在上述版本中，新建和恢复会话都会出现首条消息之前没有
可用生命周期上报的情况。只配置 `sessionStart` 不足以保证刚启动就进入 Agents 列表。
包装不会偷偷发一条消息，不会请求模型，也不会改 CLI 参数或退出码。

## 安装

先在运行 agent 的 Mac 上确认：

```zsh
copilot --version
hc --version
herdr-app --version
whence -va copilot
```

如果已有同名 `copilot` alias/function，先检查是否能合并；本项目的启动包装会定义同名
zsh 函数。不希望修改命令入口时，可以使用下文的 hooks-only 安装。

```zsh
gh repo clone leoncao1215/hc-copilot-hook
cd hc-copilot-hook

# 默认用户目录；显式同意在 zsh 配置末尾添加一个带标记的 source 块。
python3 install.py install --zshrc "${ZDOTDIR:-$HOME}/.zshrc"
```

安装器使用运行它的 Python 路径，生成正确引用本机路径的配置。
使用 `COPILOT_HOME` 时自动遵循该目录，也可以显式传入：

```zsh
python3 install.py install --copilot-home "/path/to/copilot-home" \
  --zshrc "${ZDOTDIR:-$HOME}/.zshrc"
```

新 shell 自动生效；现有 shell 可以只加载包装，不必重新执行整个 `.zshrc`：

```zsh
source "${COPILOT_HOME:-$HOME/.copilot}/herdr-app-status/copilot.zsh"
copilot --resume
```

已运行的 Copilot 需要退出后恢复会话来可靠加载 hooks。
**不需要重启 Herdr App 或 hc；不要为了重载而停止 core。**

仅安装生命周期 hooks、不修改 shell：

```zsh
python3 install.py install
```

此模式在首条消息之前可能不显示 agent。重新执行带 `--zshrc` 的安装命令即可添加启动包装。

## 配置、备份与更新

默认安装内容：

```text
~/.copilot/
├── hooks/herdr-app-status.json
├── herdr-app-status/
│   ├── bridge.py
│   ├── startup.py
│   ├── copilot.zsh
│   ├── installation.json
│   └── state/                 # 运行时自动生成，不含提示词或工具结果
└── backups/hc-copilot-hook-*/  # 修改前的文件与原始路径索引
```

安装器不改 `settings.json`，不删除已有 hooks，也不改旧 TUI 集成。
更新时运行相同安装命令：先备份专属文件及涉及的 `.zshrc`，再原子替换各个文件；
shell 标记块不会重复追加。备份权限为私有，但 `.zshrc` 备份可能含原有敏感内容，
**不能提交、上传或贴出备份**。多文件更新不是整体事务；运行失败时先检查输出和备份。

安装器拒绝覆盖同名但没有本项目安装清单的文件，也拒绝修改符号链接。
如果之前手工安装过原型，先备份并检查手工配置，再移走对应专属文件/旧加载行；
不要删除整个 `.copilot` 或覆盖整份 `.zshrc`。

## 实际验证

一定要确认 **Herdr 收到了状态**，不能只看 hook 进程退出成功。
在 App 新建两个独立 Copilot 窗格，或给已有测试终端添加 App 布局。
用 `hc ls --json` / `herdr-app ls` 找到测试终端地址：

```zsh
hc get <terminal> --json
herdr-app ls
```

刚启动时应在 core 记录里看到：

```json
{
  "reported": true,
  "status": {"app": "copilot", "state": "idle"}
}
```

依次检查新建、`--resume`、普通消息、真实问题表单、普通授权请求、正常回合结束。
保留一个窗格等待回答，让另一个完成，确认前者不被覆盖。
用独立测试会话验证后台子代理完成不会把等待中的主会话标成 `done`。
如果测试涉及命令授权，使用无害命令，只进行单次批准或取消，不能开启全局自动批准。

`hc get` 反映 core 原始状态；App 收到事件有短暂延迟，而且查看过的 `done`
会显示为 `idle`。不要把“本轮结束”理解成“所有后台工作都结束”。

本项目在上述实测版本中验证了正常状态、两个窗格隔离、后台子代理完成隔离、
上报锁失败时 Copilot 仍能回答，以及新建/恢复会话首条消息前的识别。

## 可靠性边界

- **取消/中断不完整**：Esc 取消授权或问题后可能残留 `blocked`，直到下一次输入或
  后续主会话事件。不要在未看屏幕前自动 `hc send --force`：那可能回答真正的授权弹窗。
- **授权后恢复不即时**：可能要等工具完成才由 `postToolUse` 恢复 `working`。
  hooks 无法完整描述并发弹窗、后台唤醒或所有错误/中断状态。
- **包装有意保守**：仅在有 `HC_TERMINAL_ID` 且标准输入/输出为终端的前台交互调用中运行。
  帮助、版本、子命令、`-p`、ACP、管道、未知启动选项不会主动上报初始状态。
  `command copilot`、`env ... copilot`、绝对路径会绕过 shell 函数；
  后续仍可由 hooks 上报。需要环境变量时使用 `NAME=value copilot`。
- **不是通用隔离层**：前台进程组和祖先校验用于避免误报，不是安全沙箱。
  正常后台子代理的身份字段及完成通知会被过滤；上游若不提供来源信息，
  无法对所有 notification 来源保证完美区分。
- **失败宁可漏报**：非阻塞写入/锁、0.4 秒内部时限和 1 秒 hook 时限；
  超时、锁竞争、Python 不可用、终端权限或沙箱限制都可能导致事件丢失。
  不自动放宽 sandbox。高负载和竞争下不保证每个状态都送达。
- **没有审批控制 hooks**：不注册 `preToolUse` / `permissionRequest`，不输出
  allow/deny/block 或额外模型上下文，不修改既有审批行为。
- **不读屏、不经网络发状态**：上报写到自身 `/dev/tty`，按
  `HC_TERMINAL_ID + 前台 Copilot PID` 隔离状态文件，并校验 session/timestamp。
  不能把 hooks 的 stdout 当终端输出；它是 Copilot 的 hook 返回值通道。

## 卸载

在仓库中：

```zsh
python3 install.py uninstall
# 非默认目录：
python3 install.py uninstall --copilot-home "/path/to/copilot-home"
```

仅删除安装清单中专属文件和带标记的 shell 加载块，不回滚整份用户配置。
如果专属文件被手工修改，卸载会拒绝删除；先检查并保留修改。
运行状态元数据和备份保留，不再执行。
退出并恢复 Copilot；打开新 shell，或在旧 shell 执行 `unfunction copilot`。

没有仓库时，可先将 `hooks/herdr-app-status.json` 移出 `hooks/` 目录，
移除 `.zshrc` 的 `BEGIN/END hc-copilot-hook` 块并重启 shell/Copilot。
不要删除 `hooks/herdr-agent-state.sh`：它可能属于旧版 TUI。

## 给 agent 的入口与开发

请先读 [AGENTS.md](AGENTS.md)。用户可以把下面这段交给 agent：

> 阅读这个仓库的 AGENTS.md 和 README.md。在实际运行 Copilot/hc 的机器上检查官方支持，
> 确认用户目录和 shell；先备份，再安装。保留已有设置、审批行为和旧 TUI 集成。
> 用独立测试窗格验证新建/恢复会话首条消息前的 idle，以及后续 working/blocked/done。
> 不触碰现有会话，不把密钥、配置备份或对话记录提交到仓库。

```zsh
python3 -B -m unittest discover -s tests -v
git diff --check
```

测试仅使用临时目录和 PTY，不修改真实用户配置，不调用模型或联网。

## 仓库推送密钥

Deploy key 是 **GitHub 仓库认证**，与状态 hook 无关。只给此仓库授予需要的写权限；
私钥放在仓库外、权限 `600`，公钥登记在仓库 Settings → Deploy keys。
自动化用无口令私钥时更应限制文件访问，不要分享私钥或把它写入 README/AGENTS。
可以用仓库局部的 `core.sshCommand` 指定密钥，避免影响其他仓库；SSH remote
应是 `git@github.com:leoncao1215/hc-copilot-hook.git`。

## 依据

- [GitHub Copilot hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference)
- [Copilot OSC 7501 支持请求](https://github.com/github/copilot-cli/issues/5112)
- [Herdr core](https://herdr.dev/core/)
- [Herdr App 扩展说明](https://github.com/herdrdev/herdr-app/blob/master/defaults/AGENTS.md)
- [OSC 7501 Program Status Protocol](https://www.superlogical.com/rex/docs/build/program-status)
