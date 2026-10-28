# Astrspire 与数据监视工作台架构

## 自动更新 · 2026-10-27

首个安装版定为 `0.7.0`，正式结构为“稳定启动入口 + 版本目录 + 独立更新助手”；旧便携 EXE/目录保留，不自动迁移。详细边界见 `AUTO_UPDATE.md`，发布与密钥步骤见 `RELEASE_GUIDE.md`，实测与未执行项见 `UPDATE_TEST_REPORT.md`。

更新模块位于 `src/qx_stc_isp/updates/`：`config`（标识、路径、偏好）、`manifest`（先验签后解析、`VerifiedUpdate`）、`http`（HTTPS 与主机白名单）、`checker`（匿名 latest release、ETag/304、限流与重试、下载与空间检查）、`state`（版本目录、`active.json`、事务日志）、`assistant`（等待实例退出、静默安装、健康检查、切换版本）、`launcher`（稳定入口与事务恢复）、`service`/`dialog`（Qt 工作线程与对话框）。主窗口仅新增“检查更新…”菜单项；更新前把工作区与 20 条发送历史写入 `<install_root>/data/app/recovery`。

Ed25519 仅证明发布密钥身份，不消除 SmartScreen 提示。内置公钥为空时客户端显示“未配置”，不联网、不安装。更新失败或健康检查不通过时 `active.json` 保持旧版本；不调用 `taskkill`/`TerminateProcess`。依赖新增 `cryptography==50.0.2`；schema8、串口、STC 烧录、波形与协议逻辑未改动。Inno Setup 编译与安装升级实测尚未执行，记录见测试报告。

## 暖黑默认主题 · 2026-10-27

`appearance.py` 新增 `warm` 调色板并置于 `THEMES` 首位，`ThemePalette` 增加 `focus`、`on_accent` 两个语义 token，五套旧主题同步补齐；样式表按 design.md 重写，新增 `qt_palette()` 让原生控件跟随主题。主窗口默认 `warm`，`apply_theme()` 统一设置应用调色板、重设样式表、重染波形/控件/图像/分析视图与导航图标；启动时调用一次，修复切换主题后部分视图不跟随的问题。开关按下文字改用 `palette.on_accent`，布局手柄底色与字形改用 `theme_palette()`，主题切换后重绘手柄。schema8 与业务逻辑不变，旧主题仍可选。

## dev4 实施 · 2026-10-27

当前方案和进度见 TASK_DEV4.md。新原始报文缓存与采样历史分离，界面过滤不影响记录；协议预览用私有解析器且只订阅已打开RX；发送预览、命令和控件共用纯编码入口。仅擦除会话与下载共用已知协商事务，擦除范围和写入范围分别计算。配置升级schema8并迁移旧工程，恢复无设备副作用。用户后续授权实板擦写后，独立8KiB擦除、全区预擦后的512字节下载均已收到匹配ACK；不把ACK称为全区空白回读或用户程序运行证据。

## 串口助手对照优化 · 2026-10-27

连接前由GUI明确选择DTR/RTS，SerialLinkWorker固定本轮快照，默认均关闭。PySerialTransport仅在传入控制线选项时使用port=None构造，设置DTR/RTS后显式open；这表达的是主机请求，不能保证USB驱动/适配器无电平短脉冲。未传选项的STC传输保持原行为。取消与关闭共用短时句柄锁，避免Windows OVERLAPPED对象被关闭线程清空时仍被取消线程使用；不对阻塞读写持锁。

串口助手常用配置仅由点击触发，不选择端口、不自动连接、不发送、不覆盖发送文本；连接期间锁定配置。DTR/RTS为连接选项，不加入schema7，载入工程重置关闭，其余已保存参数按原逻辑恢复。现有HEX校验、发送队列、完成计数、周期发送和COM独占不变。XCOM只作为截图/交互对照，尚无成功回包会话，不把本次软件测试写成板卡验收。

## dev3 实施边界 · 2026-10-26

批准计划见 TASK_V060.md 顶部。新增独立 SWD 数据源，读取测试固件的受限 SRAM 环形采样区，通过现有 TelemetryEvent → SampleStore → 共享 PlotBuffer → 波形/测量/CSV 链路展示。SWD 不打开 COM、不具备应用载荷发送能力；探针占用与串口占用分别管理。备份/刷写/恢复仅作为显式实验工具，不增加 STM32 烧录界面。采集进程仅本机可访问，项目包装配置关闭 target examine/reset/trace 附带写入；采集只读且不暂停CPU。

schema7 仅保存 SWD 配置，兼容schema1–6；载入不连接、不读取外部清单或执行程序。SerialWeb 串口代码复用须固定上游版本、保留AGPL-3.0和来源/修改记录，适配原生桌面传输；不自动连接、发送或嵌入网页。具体实现与实测状态按dev3验证记录逐项回填，下方历史不代表dev3完成。

实现：swd.py保存纯配置/受限清单/租约和环形读取；swd_link.py管理固定只读RPC、辅助进程及批次应答；swd_panel.py只编辑字段。连续槽采用两次批量读取并比较每槽提交号，跨环最多两段；接入先建立设备序号基线，不把接入前旧历史算成会话丢帧。波形PlotBuffer新增有界窗口峰值缓存，三卡共享只读绘图数组；含NaN的桶保留原始点，记录与测量继续读取完整数据。SerialWeb编码及分帧在独立移植模块，原生pySerial所有权和发送队列不变。

## dev2 收口 · 2026-10-25

当前批准范围见 TASK_V060.md 的 dev2 段落：STC89C52RC/既有指纹、串口助手和基础软件示波器。已以 PyQtGraph 0.14.0 替换自绘渲染，NumPy 2.5.4 提供共享只读 PlotBuffer 与基础统计；保留 WaveformWidget 接口及 schema6。原始采样仍仅由 SampleStore 保存，显示/测量不改写原始值。实现与验证状态以validation-v060-dev2.md为准。

`ProjectionBuilder`在显示线程中生成一次二维通道数组、绝对采样索引和代次；多个波形卡共享同一`PlotBuffer`，GUI不逐卡转换全历史。兼容现有分析接口的不可变列仍由同一投影提供，不是第二套绘图后端。PyQtGraph只在绘图时执行peak降采样，NaN按缺口处理，完整数组保留供测量/分析。常规Qt控件由GUI线程操作。

`MeasurementSummary`为纯统计结果，`MeasurementWorker`只接收只读数组及窗口参数，最多一个运行任务和一个最新等待任务。右栏`MeasurementPanel`绑定当前所选时间波形，200ms触发，隐藏取消待处理任务；暂停使用冻结数组。结果应用前再次匹配卡片/配置/单位/精度/代次、数组身份和可见窗口，迟到结果不覆盖当前显示。均值/RMS按幅度归一化计算以避免中间溢出，峰峰值溢出明确显示；不把缺口当零、不默认电压单位。XY仍可显示，但该测量页只对时间窗口统计。

## 当前任务 · v0.6.0

实施范围见 `TASK_V060.md`。开发预览已修正卡片刷新顺序和配置持久化，并引入`SampleCursor`代次/序号、`ProjectionBuilder`增量显示列与`ProjectionWorker`单运行/单等待任务。纯数据设置交给线程，控件只在GUI线程采用不可变快照；过期代次/配置拒绝应用。波形卡共享列和512帧峰值摘要，暂停保持冻结数据。摘要缓存有界，密集缺口退回原始显示列；缓存预算不等于整个进程内存上限。

稳定命令引用、schema7和最终v0.6.0尚未实现。原始数据、显示换算与发送保持分离；配置恢复没有连接或发送副作用。未完成项不作为现有能力声明。

> 名称说明（2026-10-22）：程序对外名称已改为 **Astrspire**，窗口标题、应用名和构建产物同步；Python 包仍为 `qx_stc_isp`，本文历史段落中的 “QX-STC ISP” 指改名前名称。

## 正在实施 · v0.5.0

已批准任务见 `TASK_V050.md`。采集线程输出带接收时间及可选来源时间的事件，单一有界 `SampleStore` 保存不可变原始采样；波形、分析、控件与导出消费快照，显示换算不修改原始值。独立记录线程承接有界写入队列，队列满/磁盘异常必须显示未保存数量。

`DashboardPage` / 卡片 / `CommandGroup` 使用稳定 ID。页面共享数据源、采样和 `ImageFrameStore`，页面切换不连接、不发送。schema5 保存页面、分组和当前页，schema1–4 迁移后仍保持无连接副作用。原生工程导入分离为纯 `NativeImportReport` 检查、预览及显式应用；无非空结构证据的内容不映射。

VOFA Demo 兼容源使用独立 CSV/JPG 时钟，经同一解码器输出，保留原逐行矩阵源。真实板卡验收是独立显式流程，不作为单元测试副作用，也不新增 STM32 烧录 UI。

## 当前增量 · v0.4.0（历史）

用户将本轮范围收紧到VOFA数据工作台，暂停全芯片扩展。下方v0.3.0段落保留历史背景，当前模块和格式以下表为补充；源码完成不等于已运行。

| 模块 | 新接口/职责 | 边界 |
|---|---|---|
| `vofa/image_protocol.py` | FireWaterImageDecoder / JustFloatImageDecoder | 只重组静态观察的封装；不操作设备，索引/尺寸/长度有界 |
| `vofa/image_formats.py` | decode_native_frame；25–34编码分派、1–24 raw | raw Qt5→Qt6对应仍是实验假设，SVG仅受限子集 |
| `vofa/images.py` / `image_effects.py` | ImageViewWidget、ImageReplayWorker、独立标准流；10种自研显示效果 | 本地目录不发送；CPU效果缩小预览；原始载荷不变 |
| `vofa/matrix_source.py` | MatrixReplayWorker / iter_matrix_rows | 无表头数值矩阵、自然排序、逐行定时；与native整文件Demo存在差异 |
| `vofa/capture_stream.py` | StreamRecorder / RawReplayWorker | 自有JSONL；RX重解码，TX不重发；128MiB上限 |
| `vofa/controls.py` / `extra_controls.py` | 13种控件、按下/松开、长按、双旋钮、四元数 | 反馈不发送；GUI仅连接packet_requested(bytes)，str信号只供观察 |
| `vofa/layout_handles.py` | 显式移动/拉伸、8px键盘、Esc取消 | 编辑布局禁用控件发送；位置随自有配置保存 |
| `vofa/control_editor.py` | 常用参数表单+附加JSON | 仅提交配置，不执行命令；无表达式求值 |
| `vofa/plugins.py` / `plugin_manager.py` | 声明式文本/定长二进制规则、管理 | 不执行Qt5 DLL/QML/Python插件；原生ABI未完成 |
| `vofa/plot_settings.py` / `waveform.py` | 时间/XY、轴/线宽/网格/颜色/游标、PNG、分析快照 | 保留显示暂停快照；只读绘图 |
| `vofa/analysis_view.py` | 柱形/直方/FFT及分析CSV | 自有算法；设定Δt假设等间隔；最多5Hz分析 |
| `vofa/channel_settings.py` | ChannelDisplay / ChannelSettingsDialog | 显示a/b/单位/精度，不改原始CSV/TX/RX |
| `appearance.py` / `theme_editor.py` | 五预设和自有JSON调色板 | 不加载任意CSS或原第三方资源 |
| `vofa/workspace.py` | schema4全设置，兼容1/2/3 | 解析先验证；不保存/恢复连接、发送、录制或采样历史 |

图像索引缓存32MiB，所选预览合并至约10Hz；目录工人每次等待GUI确认收取，离线数据回放工人没有真实传输对象。GUI `SessionEvent`/ISP状态机与 `TelemetryEvent`/数据工人继续分离，不改变既有STC核。

`ChannelDisplay`在GUI显示投影前应用；原始`_samples`与CSV继续保存原解析值。修改显示参数从最多5000帧最近原始缓存重建波形，不改变设备。分析视图读取波形的有界尾部快照，因此继承显示换算和暂停状态；FFT归一化/滤镜/自由布局均为自主设计，而不是原生算法复刻。

## v0.3.0 基础（历史说明）

范围更新（2026-10-22 / v0.3.0）：烧录后端仅面向 STC；STM32 侧对齐 VOFA+ 的界面与数据调试能力，不接入 STM32 UART ISP、ST-LINK 或 CubeProgrammer 烧录。数据源和解析器保持通用。串口 RX/TX 使用独立 `SerialLinkWorker`，TCP/UDP 使用独立 `NetworkLinkWorker`；连接本身无应用载荷 TX，显式发送通过有界队列进入工作线程。

用户进一步确认：STC 区以指定的 `AiCube-ISP-v6.97A.exe` 为完整功能对齐目标，包含芯片型号、程序/EEPROM 文件管理、连接配置、下载/重复编程、硬件选项和 STC 辅助工具。后续按官方功能建立逐项实现与验收记录；现有仅预览的状态以及尚未实现的写入选项保持明确标记。STM32 区继续以 VOFA+ 为功能和界面对齐目标。

## 两条相互隔离的工作流

## v0.5.2 显示布局

中央仍为AdjustableSplitter，默认750:250。PlotToolbar仅将同一组现有控件按可用宽度排为一行或两行，不重新创建控件或连接信号。周期设置通过QWidgetAction进入终端菜单，任务进度在进度/错误事件时展开。WorkspaceSettings保存格式仍为schema6，显式旧尺寸不覆盖；恢复默认才使用新比例。本版不修改数据投影和硬件链路。

## v0.5.1 多卡显示边界

`DashboardManager`以稳定卡片ID维护操作目标。顶部显示工具只取`active_waveform()`，没有目标即禁用；页面重建恢复卡片显示配置和共享Δt。卡片参数不回写原始采样。新分析卡保存`source_waveform_id`或显式空值（共享实时快照），页面复制映射内部ID，失效引用不自动换源。图像导出和控件编辑均由所属卡片处理，不再依赖默认页别名。

工作区写schema6、读取1–5；新增波形卡完整保存独立显示配置，旧部分配置继承既有默认值。原生导入报告分开列出`supported/ignored/unsupported/errors`，未知字段阻止完整应用。检查和恢复仍无连接或发送副作用。多卡全量投影瓶颈保留为N08，不在本版冒称解决。

```text
Intel HEX → HexImage → DownloadSession → SimulatedTransport → 模拟事件 → GUI
Intel HEX → Stc89DownloadSession → F002 / 66 43 FD 严格指纹 → 真实 ISP

用户选 COM/波特率 → PortLeaseManager → PySerialTransport.read_some
                 → VOFA 风格数据解码器 → 采样事件 → 波形/原始日志/显式 CSV 记录

手动/周期/文件/命令序列 → 有界 TX 队列 → 数据源线程 → write → 已交驱动事件
显式只读识别 → PortLeaseManager → Stc89ReadOnlySession → 7F / 状态包 → 关闭
显式 TCP/UDP → NetworkLinkWorker → 解码器 → 波形/控件/日志/CSV
自有 CSV → CsvReplayWorker → 带名称快照的采样批次 → 波形/控件（不可 TX）
```

两条流程共用只读端口枚举，但不共用状态机。当前 `DownloadSession` 只负责带 `SIM/` 标识的演示流程；不能把监视器收到的普通 UART 字节解释为 Bootloader ACK。VOFA 风格数据监视有独立的接收线程、解析器与显示状态。

## 模块边界

| 模块 | 职责 | 副作用边界 |
|---|---|---|
| `hex_image.py` / `firmware_files.py` | HEX 校验、稀疏段、BIN 基址与缓冲区导入导出 | 文件副作用；拒绝覆盖；不打开设备 |
| `chip_catalog.py` | 由 AiCube v6.97A 静态证据整理型号目录与证据级别 | 数据目录不是烧录能力声明 |
| `framing.py` / `dialects.py` | 纯帧候选与协议族证据；真实方言注册表保持为空 | 不向设备发送命令 |
| `session.py` / `simulator.py` | 手动模拟上电、阶段转换、错误注入、合成 TX/RX 事件 | 仅内存模拟 |
| `stc89.py` / `stc89_session.py` | 已观察状态/帧、只读识别和严格指纹下载核心 | 仅 F002 / 66 43 FD 注册；核心默认 allow_erase=False，GUI 显式下载才授权擦写 |
| `ports.py` | 枚举 COM 设备和标注 CH340 候选 | 不打开端口；候选标记不证明目标芯片 |
| `transport.py` / `port_lease.py` | 显式串口传输与同进程端口互斥 | 构造与租约不打开设备；显式 `open` 才访问 COM |
| `vofa/serial_link.py` / `transmit.py` | 同线程 RX/TX、有界排队、明确完成/失败/丢弃计数 | 显式连接/发送；断线不自动重试，不恢复周期 |
| `vofa/waveform.py` | 有界波形、暂停快照、缩放/拖动/游标/显隐 | 不持有设备或发命令；暂停仍可采集 |
| `vofa/network_link.py` / `playback.py` | TCP/UDP、自有 CSV 流式回放 | 显式开始才访问网络或读取文件；UDP 不学习发送目标；回放不发送 |
| `vofa/controls.py` / `commands.py` | 数值/仪表/按钮，严格命令 JSON | 载入不执行命令；按钮点击才请求发送 |
| `vofa/send_jobs.py` | GUI 线程 QTimer 驱动文件/命令顺序任务 | 用户显式开始；入队计数非 ACK；取消关闭流，GUI 另清外部队列；正常完成保留队尾 |
| `appearance.py` / `panels.py` / `ui_icons.py` | 明暗主题、原生分隔条、自绘导航 | 不操作设备；不导入第三方界面资源 |
| `vofa/` 其余模块 | 流式解析、采样事件、CSV 和 schema 3 工作区（兼容1/2） | 保存/记录需显式操作；加载不连接/发送 |
| `gui.py` | 两个工作区的操作门禁、线程与跨线程事件显示 | 仅既有严格指纹真实下载开放；监视需显式连接；发送任务互斥 |

`PortLeaseManager` 只处理本进程内互斥。VOFA 监视和 ISP 工作线程先取得租约，再打开串口；停止后先关闭句柄再释放租约。网络连接独立于 COM 租约。

## 证据与能力分离

芯片名称出现于静态识别表，只能证明目标程序包含该名称或匹配记录，不能证明当前板卡能以某种串口方言下载。帧编解码正确、HEX 解析通过、模拟状态到达 COMPLETE，也都不能代替官方工具逐帧抓包与实机烧录结果。GUI 和文档应分别显示“目录已收录”“协议候选”“实机未验证”。

VOFA+ 安装包中数据引擎的 JSON 可说明所描述的输入格式；本应用的异常处理、缓存边界、CSV 与 JSON 工作区是独立设计，不宣称与专有程序的行为或文件格式兼容。早期观察遇到授权模态，后续用户提供了完整主界面截图；布局可据截图对齐，功能等效仍需逐项实现和验证。
# 2026-10-21 协议回放扩展

## 2026-10-27 G431 UART 测试与接收诊断

独立 `firmware/g431_uart` 仅提供本轮测试数据和自有命令，不是下载器或现有用户固件的一部分。串口工作线程区分驱动已打开、字节接收和数值帧解析三种状态；接收计数与 TX 队列统计分离。超过3秒没有期望数据时只更新状态，不自动发送或重连，不丢弃采样；RawData 不要求数值帧。离线验收使用同一解析器/状态链路，硬件模式必须显式选择并校对端口，接入测试固件前仍要求身份与可恢复备份。软件测试不隐式打开真实串口。

`stc89.py` 只做已观察字段的解析、构造与应答检查；`FrameStreamDecoder` 负责有界分片重组。`official_replay.py` 在完整预检查通过后，使用与模拟器相同的 `SessionEvent` / `SessionState` 更新 GUI。它不导入真实串口传输，不具备设备打开能力，也不注册硬件方言。A2 后真实烧录由 `stc89_session.Stc89DownloadSession` 经严格指纹注册表 `ACCEPTED_HARDWARE_PROFILES` 启用，当前仅 F002 / `66 43 FD`；GUI 显式选择端口、型号、HEX 后一键开始。用户于 2026-10-21 要求移除风险勾选和二次对话框，当前没有该确认层。

脱敏夹具仅供离线测试，原始成功记录和精确 golden 保存在忽略目录 `captures/private/`。v0.2.0 的 `Stc89BaudPlan` 从定点 REA 证据计算 F002/6.6C 的时钟和波特率参数，可复现捕获；不是跨芯片通用算法，见 `analysis/07-stc89-core-evidence.md`。

