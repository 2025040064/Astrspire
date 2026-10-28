# Astrspire · 串口烧录与数据调试工作台

## 当前工程快照（2026-10-28）

公开源码仓库：[2025040064/Astrspire](https://github.com/2025040064/Astrspire)。本机最新软件版本为 **0.7.0**；本次推送不是创建新的正式 Release。保留 Claude 的自动更新实现，并修复便携布局的数据路径、源码包缺少测试/样例、缺失工作流编辑模块及 Qt/ICU 打包冲突。dev5 的独立模型/编辑器仍有未接入主窗口的内容，不能称为全量主线调试功能已完成。

本机安装入口：`Astrspire/AstrspireLauncher.exe`；更新安装包与源码包位于 `build-release/output`。运行数据放在安装根目录 `data/app`、`data/local`，不会以用户 AppData 为默认路径。按用户最新要求，本地只保留最新版本发布物，以下历史章节中的旧产物路径仅作记录。

最新程序目录与便携目录均通过349文件哈希核验和启动健康检查，原有data哈希一致；本机安装器写Windows卸载注册项被执行环境拒绝，不能将程序文件恢复等同于该安装步骤通过，详情见本轮记录。更新的正式联网升级仍未验收。

开发运行：`uv sync --locked --group dev`，随后 `uv run --locked python -m qx_stc_isp`。离线测试：`uv run --locked pytest -q`。构建安装包：`.venv/Scripts/python.exe tools/build_update_release.py --key <仓库外的私钥路径>`；私钥与本机数据不可提交。普通源码运行不需要私钥。

字体可选，公开源码不分发本地缺少再分发许可的 PingFang 文件，使用系统字体回退。私人官方抓包不公开，相应两项本机证据测试在干净克隆中明确跳过；其余离线测试不应连接硬件。本轮结果和未完成项见[导入与清理记录](docs/PROJECT_IMPORT_2026-10-28.md)。

## 当前版本 v0.7.0 · 自动更新（2026-10-27）

新增 Inno Setup 用户级安装器、稳定启动入口与独立更新助手；正式渠道采用 onedir 与版本目录，更新清单使用 Ed25519 签名，只从固定公开仓库匿名检查 stable 版本。升级前把工作区与 20 条发送历史写入 `<install_root>/data/app/recovery`，可用“工具 → 恢复更新前工作区…”还原；恢复不连接设备、不自动发送或烧录。旧便携 EXE／目录保留。细节见 [自动更新架构](docs/AUTO_UPDATE.md)、[发布指南](docs/RELEASE_GUIDE.md)、[测试记录](docs/UPDATE_TEST_REPORT.md)。

界面默认主题改为暖黑（warm），按 design.md 重写卡片/输入/按钮/菜单/表格/滚动条/焦点样式；原生控件跟随应用调色板，波形、控件与图像视图在启动和切换主题时同步重染。五套旧主题保留，schema8 不变。

内置公钥已配置，签名私钥保留在仓库外；Windows Authenticode 证书尚未配置。Inno Setup 构建及部分安装事务已有测试记录；公开仓库在线升级尚未验收，不把源码推送称为更新链路已通过。

## v0.6.0-dev4（历史）

新增“报文 / 协议工作台”：原始HEX/ASCII、搜索过滤、被动协议预览、CRC/命令模板、连接预设与配置撤销。收发编码独立，发送前可看最终字节；原始流和文本日志可分卷，工程写schema8并兼容1–7。使用方法见 [dev4说明](docs/SERIAL_TOOLS_DEV4.md)，证据与限制见 [dev4验证](docs/validation-v060-dev4.md)。

STC新增独立8KiB用户程序区擦除及下载前可选预擦，仅限F002 / 66 43 FD。先等待冷上电再识别/擦除，不是断电擦除，不动EEPROM、ISP或选项。2026-10-27已获授权并完成实板复测：独立8KiB擦除ACK匹配，全区预擦后LED程序512字节传输的4个块ACK均匹配；未做全区空白回读，未观察LED运行。

交付名称为 dist/Astrspire-v0.6.0-dev4.exe、同名目录版及源码包，实际构建状态以验证报告为准，旧版保留。运行 uv run python -m qx_stc_isp；离线测试 uv run pytest；tools/build_windows.ps1 默认目录版，-OneFile生成单文件。单文件仍使用本机F盘解包路径，跨机器可迁移性尚未验证。

保留 [Lissio来源及Apache许可](third_party/Lissio/NOTICE.md) 与既有 SerialWeb AGPL 声明。本版不含CAN、AI、工业协议、多连接或STM32烧录；后文为历史版本记录。

## 串口助手R2与实板复测（2026-10-27）

R2完善连接参数日志和无数据提示，明确区分正常空闲、固件是否启用UART及接线条件。软件832项离线回归通过；COM7真实GUI的接收/断开/关闭两次通过，但没有板端数据，临时固件握手仍失败，60秒/10分钟采集未执行。原板128KiB Flash已完整恢复并回读一致，尚需核对实际UART接线。

新版为`dist/Astrspire-v0.6.0-dev3-serial-assistant-r2.exe`，另有同名目录版和源码包，旧版保留。记录见[本轮实板报告](docs/G431_UART_RETEST_2026-10-27.md)。本轮固件重编译失败未计成功，使用与原交付归档完全一致的已知固件完成受限实验；构建残留问题另列于报告，不影响已确认的Flash恢复。

## 串口助手优化版（2026-10-27）

数据页新增“串口助手 · 常用配置”：点击后填入115200/8N1、RawData、UTF-8文本收发、CRLF，DTR/RTS关闭；仍需手动选择端口、确认并连接，不会发送。保留原发送内容、HEX模式、周期/文件发送、独立RX/TX计数和磁盘记录。

连接前可分别设置DTR/RTS，打开端口前先向驱动提交初始状态；驱动/适配器仍可能出现电平短脉冲。连接中选项锁定，断开后可修改。DTR/RTS暂不保存到工作区，加载工程后恢复关闭，其余schema7设置兼容。修复取消与关闭串口竞争；STC未传控制线选项，原下载流程不变。本轮只参考XCOM可见交互，不复制其程序代码或资源，也不宣称已证明板端收发成功。

独立优化版：`dist/Astrspire-v0.6.0-dev3-serial-assistant.exe`；旧版保留。重新构建：`powershell -NoProfile -File tools/build_windows.ps1 -OneFile -NameSuffix '-serial-assistant'`（省略`-OneFile`生成目录版）。验证记录见[串口助手对照优化](docs/XCOM_SERIAL_OPTIMIZATION_2026-10-27.md)。

## 当前开发版 v0.6.0-dev3（2026-10-26）

新增 **SWD 内存采集** 数据源：通过 ST-LINK / CMSIS-DAP 读取独立 HAL 测试固件的 SRAM 采样区，接入波形、测量和CSV。它不是UART串口，也不是电压探头；本版不提供STM32烧录界面。实验脚本独立，必须双备份、写入校验、结束恢复。G431/F103/STC分别记录结果，不能互相替代。

串口编码与CR/LF分帧已移植自[SerialWeb](https://github.com/Conductance-lab/SerialWeb)，固定提交`ff84c5d43dde6b6c49cddc1b7b01f9c55e3ece1a`，**AGPL-3.0**。这不是仅借鉴界面；完整来源、许可证和改动说明见[第三方声明](third_party/SerialWeb/NOTICE.md)。保留本地pySerial传输、端口互斥、严格HEX、显式发送和取消；不导入网页自动连接、自动应答或访问统计，不宣称完整SerialWeb兼容。分发融合版本须遵守相应源码和许可要求，本项目不自动发布。

工作区写schema7、读取schema1–6。旧工程可以载入；新工程不能承诺被旧版读取，请保留旧文件。加载不连接、不发送、不运行OpenOCD，也不读取采集清单。接收菜单增加ASCII/UTF-16LE，手动文本发送使用所选文本编码；命令组保留其显式字节约定。

SWD需要本机已有OpenOCD，可设置`ASTRSPIRE_OPENOCD`及`ASTRSPIRE_OPENOCD_SCRIPTS`指定路径；路径不从工作区执行。默认轮询50ms，清单只接受本轮16通道/64槽HAL布局，由ELF实际地址生成，不支持任意变量表达式。点击连接才读清单并启动辅助进程；SWD模式所有发送入口禁用。

dev3产物为`dist/Astrspire-v0.6.0-dev3.exe`、同名目录版及源码包；旧dev2保留。目录版请复制整个目录；单文件仍使用F盘解包路径。实际结果见[dev3验证](docs/validation-v060-dev3.md)、[分板记录](docs/hardware-v060-dev3.md)；未通过项不计完成。下面保留旧版记录。

## 当前开发预览 v0.6.0-dev2（2026-10-25）

聚焦三个用途：既有严格指纹的 **STC89C52RC 烧录**、**STM32 等设备的串口助手**、**基础软件示波器**。软件示波器显示设备发送的数字采样，不直接测量引脚电压；不提供 STM32 烧录，不宣称全芯片或完整 VOFA 等效。

- 波形改用 PyQtGraph 0.14.0，NumPy 2.5.4 提供后台生成的共享只读数组。保留16通道、100000点、时间/XY、暂停快照、缩放/平移、游标、固定轴、颜色和PNG导出，继续使用schema6。
- 右栏新增“测量”：选择时间波形后，按当前可见窗口统计有效点数、最小/最大、平均值、峰峰值和RMS。使用完整显示数据而非降采样点；暂停冻结，隐藏停止计算，最多5次/秒。单位来自通道设置，未配置时不标电压。
- STC下载核不变，仅接受F002 / `66 43 FD`；未知芯片/Bootloader在擦除前拒绝。串口收发、记录与ISP保持端口互斥，恢复工程不自动连接或发送。

离线回归 **763项通过 / 45.42s**。单通道/100000点/三卡，从投影到离屏渲染的稳态中位值约28.2ms；16通道约254.1ms，不能按相同速度声明。运行记录及持续测试以[dev2验证报告](docs/validation-v060-dev2.md)为准。最新硬件条件见[实板记录](docs/hardware-v060-dev2.md)，本机回环不代表实板验收。

源码运行：`uv sync --locked --group dev`，然后 `uv run --locked python -m qx_stc_isp`。已有环境可直接运行 `.venv\Scripts\python.exe -m qx_stc_isp`。依赖锁为`uv.lock`，新增依赖的Windows二进制兼容检查已通过，未换成其他版本。

构建脚本输出`dist/Astrspire-v0.6.0-dev2.exe`和`dist/Astrspire-v0.6.0-dev2/`，旧dev1保留；源码包为`dist/Astrspire-v0.6.0-dev2-source.zip`。目录版需完整复制目录。当前onefile使用本机F盘解包路径，无F盘电脑尚未验证；优先用目录版跨机。可控缓存、构建、日志、证据均位于F盘任务目录。

详见[操作说明](docs/VOFA_WORKBENCH_GUIDE.md)、[功能边界](docs/feature-status-0.6.0-dev2.md)、[依赖说明](docs/dependencies-v060-dev2.md)。

## 开发预览 v0.6.0-dev1 历史记录（2026-10-23）

本阶段修复八类数据显示、图像配置和原生导入问题，并接入增量采样游标、独立显示线程、三卡共享列与峰值摘要。仍使用schema6；稳定命令引用、动态命令库模板和schema7尚未实现，不是v0.6.0最终版。详见[阶段记录](docs/validation-v060-dev1.md)和[后续任务](docs/TASK_V060.md)。本轮没有操作板卡。

开发预览构建产物名为`Astrspire-v0.6.0-dev1`，保留旧版。单文件仍在F盘解包，无F盘电脑的便携性未验证。

## v0.5.2 历史版本（2026-10-23）

仅放大中央波形区域并收紧工具条/终端，默认75:25、宽窗口一行工具、窄窗口两行；周期发送设置在终端菜单，任务进度/错误保持可见。旧工程保存比例不变，schema6不升级。详见[本版说明](docs/feature-status-0.5.2.md)和[验证记录](docs/validation-v052.md)。数据缺陷、性能和命令绑定未在本版修复。

构建命令保持不变，新产物为`dist/Astrspire-v0.5.2.exe`和`dist/Astrspire-v0.5.2/`；旧版保留。可控缓存、测试、构建及解包路径均在F盘。

## v0.5.1 历史记录（2026-10-23）

修复多卡页操作目标、卡片参数恢复、控件完整属性编辑、图像卡导出、默认通道命名和原生导入漏报。每张波形标题旁点击“操作此卡”，顶部操作只作用于该卡；Δt、通道名称和换算仍共享。工作区升级schema6，可读取schema1–5；旧版本不能读取schema6，请保留原工程文件。

运行：`uv run python -m qx_stc_isp`；离线测试：`uv run pytest`。打包仍用`tools\build_windows.ps1`，加`-OneFile`生成单文件。新产物名为`Astrspire-v0.5.1`，旧版保留。全部可控构建、缓存、运行解包及证据目录保持在F盘；onefile跨电脑无F盘运行尚未验证。

见[本版功能与限制](docs/feature-status-0.5.1.md)、[本轮验证记录](docs/validation-v051.md)、[后续任务](docs/NEXT_VOFA_V051.md)。本轮不刷板，不宣称完整VOFA兼容；多卡性能优化和效果参数对齐仍待后续完成。

## v0.5.0 历史记录（2026-10-23）

新增共享100000帧原始采样、来源时间保留、异步记录、用户混合卡页、命令分组、schema5及旧格式迁移、原生导入诊断预览、CSV/JPG并行Demo。完整边界见 [当前功能表](docs/feature-status-0.5.0.md)、[测试报告](docs/validation-v050.md)、[三板记录](docs/hardware-v050.md)。不宣称完整VOFA等效。

运行：`uv run python -m qx_stc_isp`；测试：`uv run pytest`。不自动安装驱动或依赖；可直接使用现有 `.venv\Scripts\python.exe`。

构建：`powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows.ps1`，加 `-OneFile` 生成单文件。产物为 `dist\Astrspire-v0.5.0\`（需整个目录）和 `dist\Astrspire-v0.5.0.exe`；旧版本保留。onefile按本机策略在F盘解包，其他电脑无F盘时未验证。

本轮STM32只有ST-LINK、无USB/UART数据连接，不刷测试固件；F103当前Flash已备份，不能找回此前丢失原程序。STC无恢复HEX，不擦写。

## 以下为 v0.4.0 历史记录

Windows / Python 3.12 / PySide6 的 **v0.4.0 预览版**，程序名 **Astrspire**（2026-10-22 由 QX-STC ISP 改名；Python 包路径仍为 `qx_stc_isp`，仅内部标识）。STC 区提供固件文件管理、只读识别和受限 ISP 下载；数据区是独立编写的 VOFA 风格工作台。本轮优先 VOFA，不扩大全芯片烧录范围；不分发官方代码、程序、插件或界面资源。

**真实 STC 下载仍仅面向严格指纹 F002 / `66 43 FD`（Bootloader 6.6C，STC89C52RC/LE52RC）。1345 项静态芯片目录不等于 1345 个型号可烧录。STM32 仅数据调试，不提供烧录。**

## v0.4.0 已编写功能（未运行验收）

- 自有白蓝侧栏、中央波形/日志和右侧数据布局；主要区域可左右或上下拖动分隔。五套主题、自定义颜色编辑/导入导出；不加载任意 CSS 或第三方素材。界面字体使用随包加载的**苹方 SC**（Regular/Medium/Bold，约 32 MB）；系统装有 Apple **SF Pro** 时拉丁字符优先 SF Pro，否则用苹方，最后回退微软雅黑。
- 工具菜单或F1可打开软件内帮助，随EXE包含操作说明和功能状态，不需联网。
- Intel HEX 校验、BIN 显式基址、程序/EEPROM 文件缓冲区查看和新文件导出；EEPROM 缓冲区不写设备。保留 ISP 模拟、官方会话离线回放与只读识别。
- FireWater / JustFloat / RawData、16 通道时间/XY 波形、显隐、暂停快照、缩放/平移/游标、网格/线宽/固定轴/通道颜色和 PNG 导出。通道可设置名称、显示比例/偏移、单位和独立精度；原始 CSV 不做显示换算。
- 最新值柱状图、最近最多8192点的直方图、64–2048点单边幅度频谱、Hann/矩形窗、去直流和分析 CSV 导出。频谱使用设定 Δt，要求等间隔采样；不是设备采样时钟测量，也不是原生算法等效声明。
- 手动连接串口、TCP 客户端、TCP 服务器和 UDP；网络显式填写 IPv4 地址与端口。CSV 流式回放支持暂停、0.1–20 倍速及循环；回放不是设备通信。
- 13种自主控件：数值、指针/环形仪表、按钮、滑块、经典/扁平旋钮、开关、长按按钮、摇杆、指示灯、姿态立方体和图像卡片。姿态控件支持欧拉角/四元数、角度或弧度、偏移、比例和本地 STL/OBJ 线框模型（最多 4000 面，读取失败回退立方体）。图像卡片可选本地图片或实时索引0–15，实时卡片共享有界解码缓存、隐藏时不解码。可编辑表单参数、按下/松开命令、文本/HEX及结束符。接收反馈和配置导入不发送；长按/拖动取消不补发命令。
- 控件支持自适应排列/自由布局；勾选“编辑布局”后用角标移动、右下角拉伸，方向键每次8像素，Esc取消。编辑时禁用控件发送；保存工作区可恢复位置/尺寸，不恢复编辑状态。
- 图片目录回放、暂停/循环/帧率、多索引图像缓存；独立 JPEG/PNG 字节流，以及基于本轮 REA 静态证据的 FireWaterImage / JustFloatImage 封装。编码格式25–34有读取适配，实际依赖 Qt 解码器；原始格式1–24的 Qt5→Qt6 数值对应仍属兼容假设，SVG仅静态安全子集，GIF只显示首帧。
- 图像缩放/平移/旋转和20种自主显示效果：原图、灰度、反色、黑白、像素化、模糊、锐化、边缘、浮雕、复古、海报化、卡通、泛光、暗角、扫描线、波普、移轴模糊、色相隔离、冲击波、摆动；每种提供一项有界参数（强度/阈值/块大小/伽马/色相/间距/幅度）。参数名与数量参考安装包静态观察，算法为自有 CPU 实现，不是原生 shader 等效；新增CPU效果使用有界缩小预览。编码图保存原始字节，原始像素保存当前视图 PNG；可另行导出载荷，不覆盖已有文件。
- 自有原始收发 JSONL 录制与 RX 回放（不重发 TX）；数值矩阵 CSV 文件/目录回放；自有 JSON 数据引擎扩展（文本分隔或定长二进制），不执行 DLL/QML/Python 插件代码。
- 命令编辑、复制、拖动排序、选中发送、JSON 保存/载入。自有完整工作区 schema4，兼容1/2/3，保存命令、控件、布局、引擎扩展、主题、图像/绘图/终端/串口设置。导入不连接、不回放、不记录、不恢复发送。另提供“导入 VOFA+ 配置”：只读解析原版 1.3.10 的 `vofa+.config.json` 及同目录 `tabviews/cmds`，导入连接、串口帧格式、Δt、缓冲、通道显示、精度、编码和发送模式；含原生卡页/控件或命令组时明确拒绝，不静默丢弃。
- 原始文件分包与命令列表顺序/重复发送：文件非空且最大 16 MiB，界面默认 1024 字节/包、间隔 20 ms；顺序发送重复 1–10000 次，间隔 20–60000 ms。均须显式开始，不能代替 ISP 协议。**进度是已入队字节/包数，不是设备 ACK，不代表接收、执行或写入成功。**

断开和关闭窗口会停止发送任务。显式取消会清待发队列，自然完成保留最后包；停止无法撤回已发送内容，失败不自动重试。串口监视与 ISP 同进程互斥。**尚未完成全部 VOFA 功能**：原生 Qt5 DLL/QML 插件、原生顶层工程/记录格式、完整三维模型与坐标约定、全部效果参数/ANSI终端语义及运行等效均未完成或未确认。

逐项边界见 [v0.4.0 功能状态](docs/feature-status-0.4.0.md)、[使用说明](docs/VOFA_WORKBENCH_GUIDE.md) 和 [本轮进度](docs/PROGRESS_2026-10-22-VOFA.md)。静态来源见 [REA功能证据](docs/analysis/10-vofa-static-feature-inventory-2026-10-22.md)。

## 启动

可控缓存、临时文件及交付文件保持在 F 盘。以下是使用方法，不表示本轮执行过依赖安装或启动验收：

```powershell
$taskRoot = 'F:\A_LIU_Astrspire\VS\51烧录'
Set-Location -LiteralPath $taskRoot
$taskTemp = Join-Path $taskRoot 'build-preview\runtime-v040\temp'
$taskCache = Join-Path $taskRoot '.cache\uv'
New-Item -ItemType Directory -Path $taskTemp,$taskCache -Force | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:UV_CACHE_DIR = $taskCache
$env:PYTHONDONTWRITEBYTECODE = '1'
uv sync --group dev
uv run python -B -m qx_stc_isp
```

依赖约束及锁定结果见 `pyproject.toml`、`uv.lock`。Frida 仅属于可选 `capture` 依赖组，普通使用不需要安装。日志默认位于源码工程或 EXE 所在目录的 `logs`；请将工程、EXE、日志、导出和临时目录放在 F 盘。

## Windows 打包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows.ps1
# 可选单文件：
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows.ps1 -OneFile
```

默认 `onedir` 输出 `dist\Astrspire-v0.4.0\`；单文件输出 `dist\Astrspire-v0.4.0.exe`。改名后两种产物均已重建并验证启动标题 `Astrspire v0.4.0`：

| 产物 | 大小 | 说明 |
|---|---:|---|
| `dist\Astrspire-v0.4.0.exe` | 68,707,844 字节 | onefile，SHA-256 `3A76C595711DBC131EAD00311BFE7476C03E09CD41063F224179BA218909CCE6` |
| `dist\Astrspire-v0.4.0\` | 154,849,107 字节 | onedir，启动 EXE 2,550,686 字节，需完整 `_internal` |

历史产物已按同一规则改名：`Astrspire-v0.1.0`～`v0.3.0`、`Astrspire-v0.4.0-pre-live-image.exe`、`Astrspire-v0.4.0-pre-rename.exe`、`Astrspire-v0.4.0-onedir\`（改名前的旧 onedir）。改名前无版本号的 `QX-STC-ISP-preview.exe` 按最早构建记为 `Astrspire-v0.1.0.exe`。目录版需完整`_internal`，不能只复制启动EXE。旧版保留，不覆盖。

脚本将构建缓存、临时文件和 onefile 解包目录放在本机 F 工程。单文件包含本机固定 F 盘解包路径，跨机器可迁移性未验证，不能据此宣称通用便携。不捆绑官方程序或私有抓包。

## 本轮验证状态

2026-10-22 续接已执行完整离线套件：**617 passed in 23.07s**，并修复 `controls.py` 缺少 `import re` 的真实缺陷。另完成 COM8 串口打开/发送/2400 Bootloader RX/关闭、COM7 STM32G431 只读监听、本机 TCP 60 秒吞吐和最新 onefile EXE 启动/数据页验收；原版 VOFA+ 已启动做默认界面截图对照（未授权状态，未做逐效果像素对比）。详见 [实时调试与 EXE 验收](docs/validation-2026-10-22-v040-acceptance.md) 和 [渲染对照与串口回归](docs/validation-2026-10-22-render-serial.md)。串口连续 RX 吞吐仍需回环或第二数据源，更高码率/更长时间/断线恢复未验证。此前 v0.3.0 的 558 passed / 4 failed 及历史 A2 不代表本版运行结果。本轮 REA 是只读静态取证，不是通信或功能实测。

历史 A1/A2 是 Claude 在 2026-10-21 的独立验收，不是本轮新结果：[A1 只读识别](docs/validation-a1-claude-2026-10-21.md)、[A2 严格指纹下载](docs/validation-a2-claude-2026-10-21.md)、[历史进度](docs/PROGRESS_2026-10-21.md)。`captures/private/codex-readonly-v030-20261021.jsonl` 仅是只读识别证据，不能作为本轮下载成功证据。

## 使用边界与参考

枚举端口不打开设备；数据连接及配置载入不自动发送。本机端口分配：**COM8 为 STC89C52RC 烧录/识别板，COM7 为 STM32G431RBT6，仅用于数据调试，不做烧录**。发送由按钮、回车、手动周期/任务或按钮控件触发。STC89 只读识别会发送 `7F` 同步。打开 COM 可能改变 DTR/RTS 电平；停止任务也不能消除已发生的设备动作。未知指纹拒绝真实擦写，不扩大到全部目录，不修改选项字节、不写设备 EEPROM、不自动控制供电。

[架构](docs/architecture.md) · [安全说明](docs/safety.md) · [REA 下载与接入](docs/REA_SETUP_FOR_CLAUDE.md) · [AiCube 分析](docs/analysis/03-aicube-v697a.md) · [芯片证据矩阵](docs/analysis/chip-coverage.md) · [核心协议证据](docs/analysis/07-stc89-core-evidence.md) · [后续实施方案](CLAUDE_CODE_IMPLEMENTATION_PLAN.md)

旧文档描述其各自日期的范围；当前状态以本 README 和 v0.4.0 状态表为准。静态分析、源码编写、模拟、构建和硬件验收分别记录，不能互相代替。
