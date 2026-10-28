# Claude Code 的 REA 下载、接入与受限验证

日期：2026-10-21。适用项目：`F:\A_LIU_Astrspire\VS\51烧录`。

本文是交接操作说明，不是安装完成证明。编写本文时没有下载或安装软件，没有修改 MCP、客户端配置、环境变量或 PATH，也没有运行目标 EXE、打开串口或操作硬件。

## 1. 先判断是否需要安装

交接时现有会话已经提供 REA 工具。新接手的 Claude Code 应先检查自己的可用工具：如果 REA 已连接且能处理本任务，直接复用现有连接与证据，不重复 setup、不自动升级。

必须区分三层状态：

- REA 技能：提供操作说明；安装技能不代表 MCP 已注册。
- REA MCP：让当前 Claude Code 会话实际看到并调用工具；`doctor` 成功不能替代当前会话连接证据。
- 原生分析引擎：处理 EXE 所需的 Ghidra 与 Java；MCP 可见不等于任一引擎都可用。

项目原有记录使用 REA `6.5.0`、Ghidra `12.1.4`、JDK `21.0.12.1`，见 [环境基线](analysis/01-environment.md) 和 [最近的受限静态分析证据](analysis/07-stc89-core-evidence.md)。优先定位并复用，不因本文重新安装。记录中的版本不是对新客户端当前状态的独立复核。

## 2. 官方下载入口与版本边界

| 对象 | 官方来源 | 说明 |
|---|---|---|
| REA 项目源码 | [morluto/rea](https://github.com/morluto/rea) | 页面选择 `Code → Download ZIP` 可下载源码；源码 ZIP 不是包含 Node、MCP 注册、Ghidra 和 JDK 的完整运行环境 |
| REA 安装说明 | [docs/installation.md](https://github.com/morluto/rea/blob/main/docs/installation.md) | 阅读对应版本说明；不要将 main 的全部参数套用于旧包 |
| REA npm 包 | [rea-agents](https://www.npmjs.com/package/rea-agents) | 本项目先固定使用 `rea-agents@6.5.0` |
| Node.js | [官方 Windows 下载](https://nodejs.org/en/download) | 仅在本机确实缺失或版本不兼容、且获准后安装 |
| Ghidra | [NSA 官方 Releases](https://github.com/NationalSecurityAgency/ghidra/releases) | 按所选 Ghidra 版本的随附安装说明确认 Java 要求 |
| JDK | [Oracle Java 官方下载](https://www.oracle.com/java/technologies/downloads/) | 选择满足该 Ghidra 版本要求且授权适用的 JDK；现有可用 JDK 无须重装 |

交接时已核对的 REA main 安装示例为 `6.7.0`，列出的 Node 支持范围为 22.x ≥ `22.19`、24.x ≥ `24.11` 或 26+。这些是 main 的说明，不证明 `6.5.0` 拥有 main 的新功能或全部相同选项。复现本项目先固定 `6.5.0`；若未来升级，应另列升级范围和兼容性验证，不混用版本命令。下载 main 源码 ZIP 也不等于取得 `6.5.0` 源码；如需源码固定版本，应选择已核实的对应提交或发布记录，不猜测 tag 名称。

### npm 获取方式：按需运行优先

下面只是待批准执行的命令示例。`npx` 即使运行 `doctor` 或 `--dry-run`，也可能从 npm 下载包并写入缓存；它们并非零文件系统副作用。尚未获准获取软件时，先复用已有 REA 启动器与工具。

```powershell
npx -y rea-agents@6.5.0 doctor --client claude_code --json
```

可选的全局安装会修改 npm 全局安装目录，需要单独批准；并非 MCP 接入的前提：

```powershell
npm install --global rea-agents@6.5.0
```

只采用一种包获取方式即可。全局包安装与 MCP 注册是两回事；安装完成仍需核对实际客户端连接。已有服务能用时，不因 shell 找不到 `rea` 而重装。

## 3. Claude Code 注册：选择一条路线

先在当前项目目录操作，确认 `claude`、Node/npm 的实际启动器可用。必要时仅查看：

```powershell
Get-Command node, npm, npx, claude -ErrorAction SilentlyContinue
node --version
npm --version
```

只有在工具缺失、注册过期或启动错误已经出现时，才运行上一节固定版本的 `doctor`。保留原始诊断输出，区分注册、当前会话连接、PATH 和引擎四类问题。

### 路线 A：项目 local 注册

适合只给本项目的 Claude Code 增加 REA。先检查是否已有同名 `rea` 条目及其作用域，记录实际配置位置并备份将修改的文件，再由用户批准新增或修复。`local` 是 Claude Code 的作用域名称，不应据此猜测一定写入仓库的 `.mcp.json`；以客户端实际报告为准。

获批后，在本项目目录执行官方 Windows 形式的注册命令：

```powershell
claude mcp add --scope local rea -- cmd.exe /d /c npx -y rea-agents@6.5.0 mcp
```

完成条件：注册指向 `6.5.0`，作用域为当前项目的 `local`，未覆盖不相关条目。选择此路线后，不再运行另一条 `setup --yes` 去重复添加全局/其他作用域的同名服务。

### 路线 B：REA 自动配置修复

适合已确认需要由 REA 修复 Claude Code 注册的情况。先只生成计划：

```powershell
npx -y rea-agents@6.5.0 setup --client claude_code --dry-run --json
```

逐项向用户展示计划中的真实路径、作用域、现有内容变化、备份位置，以及可能替换的 bundled skill。先保留受影响配置与技能的备份；确认备份可恢复后，取得针对这些变更的批准。若计划触及 Codex 或其他不在范围内的客户端，停止并缩小范围。

获批后，按同一客户端、同一路径和作用域应用；若预演另有已经核实且获批的范围参数，应用时原样保留：

```powershell
npx -y rea-agents@6.5.0 setup --client claude_code --yes
```

不要额外请求安装 Hopper；本项目已有 Ghidra 路线。REA setup 不负责安装或升级 Node、Java、Ghidra 等前置软件。完成条件是计划与实际变更相符、备份保留，而不是仅有命令退出码为 0。

### 两条路线的共同收口

重启或重连受影响的 Claude Code 会话，确认该会话实际出现 REA 工具。如果注册看似正确但仍无工具，检查客户端的 MCP 启动错误，不循环执行 setup。仅修复 Claude Code 的已批准范围，保留 Codex、其他客户端与既有全局工具链。

## 4. 原生引擎：定位已有安装，不猜绝对路径

优先使用已工作的 Ghidra `12.1.4` 与 JDK `21.0.12.1`。可查看当前 shell 信息：

```powershell
Get-Command java -ErrorAction SilentlyContinue
java -version
$env:GHIDRA_INSTALL_DIR
$env:JAVA_HOME
```

空环境变量、shell 找不到命令，并不证明 REA MCP 使用的独立启动环境缺失该软件。先查看已连接服务和相应操作的 provider 诊断，再定位实际目录。需要配置时，`GHIDRA_INSTALL_DIR` 应指向真实 Ghidra 安装根目录，`JAVA_HOME` 应指向满足该版本要求的真实 JDK 根目录；核对目录内容及 Java 版本后，另行批准最小范围变更。本文不提供猜测的安装路径，也不要求写入系统级 PATH。

遇到 `provider_unavailable` 时，只处理本次原生分析需要的 provider。可用工具目录不代表每项操作均已就绪，应按当前版本实际工具 schema 与可用性原因执行，不能为修复单个 provider 安装无关软件。

## 5. 连通性验收：受限静态查询、快照与关闭

仅完成注册不算验收完成。获准验证后，按以下顺序保留证据；这里不启动官方程序或连接硬件。

1. **当前会话工具可见。** 记录 Claude Code 实际列出的 REA 工具与可用性。已有匹配的分析会话时先复用；按需查看 `binary_session`，不要仅凭 `doctor` 宣称已连接。
2. **固定目标身份。** 使用用户指定的 `C:\Users\Legion\Desktop\AiCube-ISP-v6.97A.exe`。先核对文件存在与 SHA-256：`6194b42b9f7812e745ab19e6f5a25b474f740f84c7ac9e7cfa940cbbbfe1cbcc`。路径缺失或散列不一致时停止，询问正确文件；不要替换为附近其他版本。散列锁定分析对象，不等于发布者真实性认证。
3. **一次受限静态查询。** 优先复用匹配的已有快照；否则按当前 `open_binary` schema 静态导入该 EXE，选用已就绪的原生后端。查看一次受限概览或串口 API 静态导入查询，例如 `CreateFileA`、`ReadFile`、`WriteFile`；记录 Evidence ID、后端版本、结果范围及未知项。打开分析对象不是运行目标 EXE，静态导入也不证明真实通信行为。
4. **保存与关闭。** 按实际可见工具 schema 保存新的分析快照到已批准的 `captures/private` 新文件中，核对保存成功后调用 `close_binary` 释放本次打开的会话。不要覆盖已有 `aicube-v697a-20261021-stc89-core.snapshot.json`；复用前先核对目标散列。工具名或快照参数与 main 文档不一致时，以当前 `6.5.0` 实际 schema 为准，不编造调用。
5. **汇报结果。** 分别列出“包版本、Claude Code 注册范围、当前工具连接、原生查询、快照保存、会话关闭”的成功或失败及证据位置。任何一步未完成均明确标注，不把技能安装、静态分析或模拟测试写成实机烧录验证。

本说明不授权启动 AiCube、采集新通信、擦除或写入芯片，也不改变项目真实下载默认禁用的门禁。硬件操作与真实下载启用仍须另行获得授权，并按项目证据与验收要求执行。

## 6. 交接核对清单

- 已确认是否可复用现有 REA；未因 shell PATH 问题误判 MCP 损坏。
- REA 固定为 `6.5.0`，没有照抄 main `6.7.0` 的未核实选项。
- 如需注册，只选择路线 A 或 B；变更范围、备份和批准都有记录。
- Node/Ghidra/JDK 使用官方来源且仅处理实际缺失项；本机可用安装保留。
- Claude Code 当前工具可见，并有固定 EXE 的受限静态查询、Evidence、快照及关闭记录。
- 未修改其他客户端、未运行目标程序、未进行硬件通信；未执行的步骤逐项标注为待办。

## 7. 2026-10-21 Claude Code 实际接入状态

- REA `6.5.0` 已存在于 npx 缓存（`C:\Users\Legion\AppData\Local\npm-cache\_npx\ad9a3fdbcad992c6`），未从 GitHub 重新安装运行包。
- 复用 Codex 已有分析引擎，未重新解压或升级：
  - Ghidra `12.1.4`：`C:\Users\Legion\Documents\Codex\tools\rea-engines\ghidra_12.1.4_PUBLIC`
  - Temurin JDK `21.0.12.1+1`：`C:\Users\Legion\Documents\Codex\tools\rea-engines\jdk-21.0.12.1+1`
  - JADX bridge `0.7.1`：`C:\Users\Legion\Documents\Codex\tools\rea-engines\jadx-headless-mcp-0.7.1-all.jar`
- 已备份 `C:\Users\Legion\.claude.json` 到 `.claude.json.bak-20261021-rea`。
- 已在本项目 `local` 作用域注册 MCP，直接使用缓存中的 `rea.mjs`（避免 npx 联网），并写入上述三个环境变量。`claude mcp list` 显示 `rea: ✔ Connected`。
- `rea doctor` 的 `registration:claude_code` 仍报 missing：REA 检查的是它期望的顶层注册形态，而 Claude Code 将 local 注册保存在 `projects["F:/..."]` 下；实际连接以 `claude mcp list` 为准。Ghidra/JDK/JADX 检查均为 healthy。
- REA skill 未安装；如需要可单独运行 `rea setup --client claude_code`，它会改动 `.claude.json` 并安装到 `~/.claude/skills/reverse-engineer-anything`。
- 当前 Claude Code 会话不会热加载新 MCP；需要重启会话后在工具列表确认 REA 工具。

> 本次曾从 GitHub 下载 Ghidra/JDK/REA 源码 ZIP 到临时目录，发现已有引擎后已删除，未用下载包覆盖现有安装。
