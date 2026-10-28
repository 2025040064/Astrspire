# 已确认项目状态

更新：2026-10-22 / v0.4.0；源码存在、构建完成与运行/硬件验证是不同层级。

## 当前增量（源码与构建事实，不是运行结论）

- 用户最新要求先推进VOFA，暂停全芯片扩展。重新确认的一小时窗口为05:53:44–06:53:44 UTC；早前额度等待不计持续工作。
- 项目元数据、包`__version__`、GUI标题和构建脚本均已改为0.4.0。新增图像、格式/滤镜（强度/阈值/块大小参数）、原始记录、矩阵回放、声明式引擎/管理、控件编辑/自由布局、统计/频谱、通道属性和主题编辑源码；接口索引见SOURCE_INDEX。实时图像卡片已接入共享`ImageFrameStore`（来源local/live、索引0–15、隐藏不解码）。
- 工作区源码写schema4并兼容本项目1/2/3；独立控件写schema2并兼容1。没有原生VOFA格式兼容结论。
- 本轮REA已保存79条主目标静态Evidence，并正常关闭所拥有的native目标/分析客户端。图像头、格式分支、CSV转换及原生插件加载链在`../analysis/10-vofa-static-feature-inventory-2026-10-22.md`记录；没有启动原程序或通信取证。
- 最终v0.4.0 onefile于06:48:54 UTC写入，47,425,793字节；最终onedir于06:49:40 UTC写入，启动EXE为2,512,126字节且需完整_internal。PyInstaller均退出0，没有运行产物；详见`../PROGRESS_2026-10-22-VOFA.md`。
- 2026-10-22 续接已执行完整回归：**617 passed in 23.07s**（`build-preview/test-results-v040-filter-params-2026-10-22.xml`）。修复`controls.py`缺少`import re`的真实缺陷；实时图像卡片与滤镜参数测试通过。另完成 COM8 串口打开/发送/2400 Bootloader RX/关闭验收、本机 TCP 60 秒吞吐、最新 onefile EXE 启动与数据页验收；详见`../validation-2026-10-22-v040-acceptance.md`。串口连续 RX 吞吐未验证。
- 2026-10-22 程序对外名称改为 **Astrspire**：窗口标题、应用名、帮助标题、`pyproject.toml` 描述、构建产物名（`Astrspire-v0.4.0`）已更新，Python 包路径保持 `qx_stc_isp`。本机端口分配：COM8 为 STC89C52RC 烧录/识别板；COM7 为 STM32G431RBT6，仅用于数据调试，不烧录。改名后 onefile 重建为 `dist/Astrspire-v0.4.0.exe`（68,707,844 字节，含苹方字体），启动标题 `Astrspire v0.4.0` 已验证；相关 GUI/VOFA 测试 30 passed。
- 2026-10-22 新增只读 VOFA+ 1.3.10 原生上下文导入（vnumber 100）：读取 `vofa+.config.json` 同目录的 tabviews/cmds，导入连接、串口帧格式、Δt、缓冲、通道显示、精度、编码和发送模式；含原生卡页/控件或命令组时拒绝。实机上下文已成功导入离屏 GUI。COM7 在 9600–2000000 共 10 档只读监听均 0 字节，当前固件无输出。
- 早期静态轮次未打开 GUI、串口或网络；2026-10-22 续接已打开 GUI、COM7/COM8 和原版 VOFA+，未改 STC 下载核心/注册指纹，未下载依赖。所有可控构建/分析写入在 F 工程内；既有 C 盘工具仅复用读取。

## 历史基线（不是v0.4.0验收）

- 入口 `python -m qx_stc_isp`，打包入口 `app_launcher.py`；依赖和锁在项目根，Python 3.12.13。
- 当前源码版本 v0.3.0，有独立固件文件、串口传输/占用、帧、模拟/回放/STC89 核心、遥测/发送/绘图和 GUI 模块，见 SOURCE_INDEX。
- 通用 `HARDWARE_DIALECTS` 为空；严格指纹注册表 `ACCEPTED_HARDWARE_PROFILES` 只有 F002 / `66 43 FD`。GUI 真实烧录按钮在已选端口、型号和 HEX 后一键启动（2026-10-21 用户要求移除风险勾选与确认弹窗）；端口被本进程其他工作区占用时按钮禁用。下载核擦除前再次校验指纹。GUI 只读按钮选好端口即可打开串口并发 7F。`Stc89DownloadSession` 默认 `allow_erase=False`。
- v0.2.0 的 232 项通过数是历史 A2 报告；本轮已启动套件为 558 passed / 4 failed（CSV 通道名长度），源码修复后未复跑，后接入发送任务也未测试。用户要求后未追加验证。
- 历史 v0.2.0 EXE 指纹保留于 `../validation-0.2.0.md`；v0.3.0 使用独立产物名称，不将旧产物证据当新版本验收。
- 2026-10-22 已实际构建 `dist/Astrspire-v0.3.0.exe`（PyInstaller onefile 退出0）；未执行新版 EXE 启动/功能验收，旧 v0.2.0 保留。
- v0.3.0 源码增加 network_link/playback/controls/commands/send_jobs、明暗主题、可拖动分区和 schema3 工作区；不将编写完成等同运行验证。
- 2026-10-21 Codex 获授权完成一次限时只读识别：COM8 / F002 / 6.6C，private 记录 `captures/private/codex-readonly-v030-20261021.jsonl`。没有擦写、复位或替换板上固件；2026-10-22 未追加硬件验证。
- Claude Code 于 2026-10-21 完成 A0 基线：228 项测试通过（本机无 skip），离屏 GUI 检查确认最小窗口可滚动、发送输入可编辑、真实写入禁用、取消后线程退出；全程未打开串口。记录见 `../validation-a0-claude-2026-10-21.md`。
- Claude Code 于 2026-10-21 完成 A1 只读识别实板对照：COM8 备用板 6 次识别均为 F002 / `66 43 FD` / 6.6C / 53 字节状态包，仅发送 `7F`，端口正常释放，取消路径通过；USB 供电导致实板 30 秒超时未触发。记录见 `../validation-a1-claude-2026-10-21.md`。
- Claude Code 于 2026-10-21 完成 A2：COM8 实板写入 `led.hex` 成功，14 条 TX 与官方记录逐字节一致，块 ACK 4/4，断电重启运行；官方工具同板同文件成功；GUI 真实烧录入口已加入并完成实板验收（4 次 purge 对齐官方）。记录见 `../validation-a2-claude-2026-10-21.md`。仅该指纹开放，其他型号仍锁定。
- Claude Code 于 2026-10-21 开始 A3：新增官方 GUI 清单（`../analysis/08-aicube-gui-inventory-2026-10-21.md`）和识别表字段追踪（`../analysis/09-catalog-field-tracing-2026-10-21.md`）；解析 1345 条 32 字节记录到 private JSON，确认 f0 位布局、class→协议族分派、F002 class 1 路径；f3/f4 容量语义仅部分确认，f5–f7 保持未知。
- REA 6.5.0 已在本项目 local 作用域注册为 MCP 并 `Connected`；复用 Codex 的 Ghidra 12.1.4、JDK 21、JADX bridge，未升级或覆盖。接入状态见 `../REA_SETUP_FOR_CLAUDE.md` 第 7 节。

历史执行细节在 `../validation-0.2.0.md`；当前实现和未复测项见 `../PROGRESS_2026-10-22-VOFA.md`。未验证的行为和未来里程碑不作验收完成声明。
