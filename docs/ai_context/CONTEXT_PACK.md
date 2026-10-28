# 接续上下文 · 2026-10-22

工程 `F:\A_LIU_Astrspire\VS\51烧录`，Python/PySide6，当前v0.4.0预览版。以下旧版记录保留历史；当前范围和限制以本节为准。

## 当前接续必读

- 最新范围：先VOFA，暂不全芯片；不改STC严格指纹。用户续接后重新要求工作1小时，窗口2026-10-22 05:53:44–06:53:44 UTC；早前额度等待不是连续开发。
- 读`../PROGRESS_2026-10-22-VOFA.md`、`../feature-status-0.4.0.md`和`../VOFA_WORKBENCH_GUIDE.md`。REA结果读`../analysis/10-vofa-static-feature-inventory-2026-10-22.md`，不要重开已关闭目标仅为了重复取证。
- 新源码：多格式/多索引图像与目录回放、10种自有滤镜（强度/阈值/块大小参数）、原始JSONL录制/RX回放、矩阵CSV、13种控件/表单/自由布局、声明式JSON引擎、主题编辑、时间/XY和统计/FFT、显示通道参数、schema4完整工作区；实时图像卡片已接入共享`ImageFrameStore`。
- 未完成完整VOFA：native顶层工程、Qt5 DLL/QML ABI、自定义3D模型/完整坐标约定、全部效果/终端语义和运行等效。不能将自有格式或算法写成恢复了原生实现。
- 2026-10-22 续接已执行完整回归：**617 passed in 23.07s**；修复`controls.py`缺少`import re`的真实缺陷，并更新v0.3遗留断言。另完成 COM8 串口打开/发送/2400 Bootloader RX/关闭验收、本机 TCP 60 秒吞吐（约1971样本/s、84.8KB/s、缓冲5000、RSS稳定）和最新 onefile EXE 启动/数据页验收；详见`../validation-2026-10-22-v040-acceptance.md`。串口连续 RX 吞吐仍需回环或第二数据源。
- 构建脚本输出v0.4.0 onefile和onedir；onefile本机固定F解包路径，跨机器未测，目录版必须保留_internal。最终构建状态看本轮进度。
- 最终单文件47,425,793字节，UTC06:48:54；最终目录版启动EXE为2,512,126字节，UTC06:49:40，两种PyInstaller均退出0。没有启动产物。`NEXT_VOFA_LIVE_IMAGE.md` 任务已完成，不再作为待办。
- 当前工作区schema4兼容本项目1/2/3；控件独立schema2兼容1。布局编辑不恢复，连接/发送/录制均不自动启动。
- STC仍仅F002 / 66 43 FD开放；全芯片未完成。所有可控临时/缓存/日志/构建保持F盘，不触碰Unimate。原始官方文件/私有快照不分发。

## 历史接续记录

用户要求：STC 对齐指定 AiCube ISP 全功能/最终全芯片；STM32 只对齐 VOFA+ 调试，不做 STM32 烧录。用户已要求继续开发，并在 2026-10-22 要求连续实施、不再追加校验；不要按旧版交接中的“收尾即停止/A0复核”自动开启新验证阶段。

先读根目录 `CLAUDE_CODE_IMPLEMENTATION_PLAN.md` 的 A0，再按 [源码索引](SOURCE_INDEX.md) 定位。REA 安装/连接有问题才读 `../REA_SETUP_FOR_CLAUDE.md`，不自动改现有 Codex 配置。

已实现：HEX/BIN 文件缓冲区、1345 项静态目录、模拟/官方会话离线回放、只读 STC89 识别；F002/6.6C 严格指纹已通过 A2 实板烧录并在 GUI 中开放（其他型号仍锁定）。VOFA 风格串口手动/周期 TX、RX 三引擎、交互波形、CSV、工作区。**全芯片/全功能未完成。**

v0.2.0 历史结果见 `../validation-0.2.0.md`。v0.3.0 中途套件为 558 passed / 4 failed；4 项 CSV 通道名称长度问题已改源码。2026-10-22 续接的完整 v0.4.0 套件为 617 passed，不能把旧 558/4 当作当前通过数。原始官方记录和 REA 快照在 `captures/private/`，不分发；公开夹具脱敏，不能冒充原始 golden。

Claude Code 接续：A0 已于 2026-10-21 复跑（228 passed，GUI 离屏基线检查通过，未打开串口），记录见 `../validation-a0-claude-2026-10-21.md`。A1 已在 COM8 备用板完成只读识别对照（6 次成功，F002/6.6C，仅 TX 7F；取消通过；实板超时未触发），见 `../validation-a1-claude-2026-10-21.md`。A2 已完成该严格指纹的真实擦写、官方逐消息对照、GUI 入口和重启运行验收（232 passed），见 `../validation-a2-claude-2026-10-21.md`。2026-10-21 用户要求移除风险勾选与确认弹窗，真实烧录现为一键启动；仅对 F002 / 66 43 FD 开放，其他型号继续锁定。

A3 已开始：官方 GUI 清单见 `../analysis/08-aicube-gui-inventory-2026-10-21.md`，识别表字段追踪见 `../analysis/09-catalog-field-tracing-2026-10-21.md`；REA 6.5.0 已注册为项目 local MCP 并连接，Ghidra 12.1.4/JDK 21 复用 `C:\Users\Legion\Documents\Codex\tools\rea-engines`，接入细节见 `../REA_SETUP_FOR_CLAUDE.md` 第 7 节。本轮完整进度与改动清单见 `../PROGRESS_2026-10-21.md`。

v0.3.0 已编写：紧凑白蓝侧栏、图标导航、全部主分区原生拖动、明暗主题；显式 TCP 客户端/服务器、UDP；自有 CSV 流式回放、暂停/倍速/循环；数值/仪表/按钮控件和命令 JSON；文件分包、命令列表顺序/重复发送；schema3 工作区兼容1/2。模拟入口移至工具菜单。无自动连接或自动发送；导入工作区不恢复发送。硬件下载仍只有 F002 / 66 43 FD。

本轮详细变更与未复测状态见 `../PROGRESS_2026-10-22.md`、`../feature-status-0.3.0.md`。优先按用户已确定的待办实施，不扩展真实擦写范围；已知缺口见 [未确认项](OPEN_QUESTIONS.md)。所有可控临时文件、构建和日志写到 F 盘。
