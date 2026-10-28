# VOFA 工作台 v0.4.0 实施记录

日期：2026-10-22。工程：`F:\A_LIU_Astrspire\VS\51烧录`。

## 范围与时间

- 最新范围为“先不搞全芯片，先完成完整VOFA功能”。本轮实际推进VOFA自主功能，不动STC下载核心和既有F002 / `66 43 FD`注册。
- 用户续接后重新要求工作1小时：05:53:44–06:53:44 UTC，本地13:53:44–14:53:44。早前额度限制导致的等待不计入连续开发。
- 按用户要求未运行测试、GUI验收、实机或网络联调。源码/静态证据/构建与运行验收分别记录；不宣称已完成完整VOFA等效。

## 本轮写入的内容

1. **图像链路**：标准JPEG/PNG流、目录回放、暂停/循环/帧率和回收；静态FW/JF图像头、按长度重组、多索引缓存；25–34编码类型和实验raw适配，受限SVG。
2. **图像显示**：缩放/平移/旋转、保存图像和原始载荷；10种自有滤镜，新增CPU效果最多16384像素/最长边256；图像栏两行，约10Hz合并预览，不反复抢页。
3. **可视控件**：13种自主控件，包括双旋钮、长按进度、按下/松开、摇杆、三色灯、Euler/四元数线框；常用参数表单、文本/HEX和结束符。
4. **布局**：自适应/自由模式，角标移动/拉伸、8px键盘、Esc/失焦取消、64步布局撤销/重做；编辑布局禁用控件发送。位置尺寸随工作区/独立控件保存，删除/替换需确认。
5. **绘图与分析**：时间/XY、轴范围/网格/线宽/颜色/多通道游标/PNG；最新柱状图、直方图、单边FFT、窗函数/去直流、分析CSV。
6. **通道属性**：名称、显示a/b、单位和独立精度；重建显示不改原始采样CSV或RX/TX。新增软件字节/帧到达速率。
7. **数据与记录**：自有矩阵CSV文件/目录、原始收发JSONL录制/RX回放；TX记录不重发。本地来源无传输对象。
8. **扩展与主题**：声明式文本/定长二进制JSON规则、扩展管理；五套预设、自有颜色编辑/导入导出，不执行第三方代码。
9. **工作区与终端**：schema4保存上述配置，兼容本项目1/2/3；命令编辑/拖动排序；终端编码/方向/时间戳/暂停/字号/查找/保存/清空；配置JSON限制重复键、非常量、深度/节点/大小。
10. **后续回归**：新增`test_vofa_v040_math.py`与`test_vofa_v040_envelopes.py`，使用明确合成数据；最初未执行，2026-10-22 续接已随完整套件执行（617 passed），不是官方golden。
11. **内置帮助**：工具菜单/F1提供操作说明和功能状态，随EXE打包，不自动打开网络链接。

## REA证据

目标是用户安装的VOFA+ 1.3.10，原EXE/插件只读。未复制其QML、shader、图标或模型。完整本轮清单见`analysis/10-vofa-static-feature-inventory-2026-10-22.md`：

- Demo CSV/JPG分支、独立定时器与自然排序。
- FW五字段ASCII图像头；JF七个LE uint32头及双`0x7F800000`尾；对应DLL接收入口。
- ImageProvider格式25–34映射；27=JPG、28=PNG；其他值传入Qt5原始格式。
- CSV逐行float32转换、逗号汇编调用点。
- 12类安装控件的配置入口、RingBufferWave/DataHub/TextBuffer元对象接口。
- 原生Qt5插件的QPluginLoader/IID链；完整ABI、顶层工程schema未确认。

主快照正常保存79条Evidence，06:07:55 UTC关闭本任务拥有的native目标，随后退出分析客户端。快照/bundle仍在`captures/private/`，不随EXE分发。静态观察不是运行结果；自有FFT、滤镜、布局和错误策略不冒称原算法。

## 构建与交付

使用已有Python3.12.13 / PyInstaller6.22.3，不安装或下载依赖。构建、缓存、临时和分析输出均指向F工程；原始程序和全局客户端未修改。

执行入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows.ps1 -OneFile
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows.ps1
```

- 初次onefile约06:28 UTC生成；onedir于06:31 UTC生成，均退出0。
- JSON边界、三色灯、布局撤销/重做、自由布局尺寸保持、F1帮助及最新随包说明均已纳入最终重新构建。
- 当时 onefile（06:48 UTC，47,425,793 字节）与 onedir（06:31 UTC，启动 EXE 2,512,126 字节）均已被后续构建覆盖；当前产物名见 README，历史文件名在此不重复。
- onefile固定解包目录在本机`build-preview/runtime-v040/bundle-run`；跨机器/无F盘部署未验证。目录版没有该onefile固定解包要求，也未启动验收。
- 不覆盖旧版，不捆绑官方程序、私有捕获/快照或第三方专有资源。

### 最终产物记录（仅打包身份，不是启动验收）

| 产物 | 文件大小 | 最后写入UTC | 构建结果 |
|---|---:|---|---|
| 当时 onefile（已被覆盖） | 47,425,793字节 | 2026-10-22 06:48:54 | onefile，退出0 |
| 当时 onedir 启动EXE（已被覆盖） | 2,512,126字节（仅启动EXE，不含依赖目录） | 2026-10-22 06:49:40 | onedir，退出0 |

目录版需要完整`_internal`，不能只复制2.5MB启动EXE。两种构建最后均由既有PyInstaller执行完成；没有运行产物。读取文件大小/时间用于交付定位，不作为应用功能检查。

## 没有完成的部分

原生DLL/QML插件执行兼容、原生顶层工程/记录格式、自定义三维模型和完整坐标语义、全部原生滤镜参数、完整ANSI终端控制、所有可达菜单和运行等效仍未完成。raw Qt5/Qt6枚举/颜色表/端序、图像解码器插件、吞吐和线程收口均未实测。

全芯片和所有ISP选项本轮按用户要求不推进。历史A1/A2只属于已报告的F002 / 6.6C备用板，不扩大结论。

## 2026-10-22 续接完成

- **实时图像卡片**：新增共享 `ImageFrameStore`；image 卡片支持 `local|live` 与索引0–15；隐藏卡片不解码；同代次只解码一次；独立图像页与卡片共享结果；删除/编辑/载入后重算订阅；清会话清缓存。详见 `NEXT_VOFA_LIVE_IMAGE.md` 完成记录。
- **滤镜参数**：灰度/反色/复古/模糊/锐化/边缘/浮雕增加强度（0–100），黑白增加阈值（0–255），像素化增加块大小（2–64）；强度0返回原图；效果参数写入schema4工作区，缺失字段按效果默认值补齐。
- **回归**：已执行完整套件，**617 passed in 23.07s**（`build-preview/test-results-v040-filter-params-2026-10-22.xml`）。修复了 `controls.py` 缺少 `import re` 的真实缺陷；更新了 v0.3 遗留的 schema3/6来源/旧精度断言。此前“未测试”状态只保留为历史。

## 2026-10-22 改名与端口分配

- 程序对外名称由 QX-STC ISP 改为 **Astrspire**：窗口标题、页头品牌、帮助窗口标题、`QApplication` 应用名、`pyproject.toml` 描述和 EXE 产物名（`Astrspire-v0.4.0`）同步更新；Python 包/导入路径仍为 `qx_stc_isp`。
- 本机串口分配：COM8 为 STC89C52RC 烧录/识别板；**COM7 为 STM32G431RBT6，仅作数据调试，不提供烧录**。改名后 GUI/实时图像/滤镜参数相关测试 30 passed；完整套件未复跑。
- 最终 onefile：`dist/Astrspire-v0.4.0.exe`，68,707,844 字节，SHA-256 `3A76C595711DBC131EAD00311BFE7476C03E09CD41063F224179BA218909CCE6`；启动验证窗口标题 `Astrspire v0.4.0`、进程响应正常，关闭后无残留。
- 最终 onedir：`dist/Astrspire-v0.4.0/`，154,849,107 字节，启动 EXE 2,550,686 字节；启动标题 `Astrspire v0.4.0` 已验证。
- `dist/` 历史产物全部改名：`Astrspire-v0.1.0`～`v0.3.0`、`Astrspire-v0.4.0-pre-live-image.exe`、`Astrspire-v0.4.0-pre-rename.exe`（改名前的 20:51 构建）、`Astrspire-v0.4.0-onedir/`（改名前的旧 onedir）。原无版本号的 `QX-STC-ISP-preview.exe` 按最早构建记为 `Astrspire-v0.1.0.exe`。
- 界面字体使用用户提供的苹方 SC（Regular/Medium/Bold，随包加载）；系统装有 SF Pro 时拉丁字符优先 SF Pro，否则苹方，最后回退 Microsoft YaHei UI。字体文件位于 `assets/fonts/`。

## 2026-10-22 图像效果静态解析与移植

- 只读分析 `E:\app\x64\plugins\widgets\image\effects` 的 14 个效果 QML，提取效果名、参数名、默认值和参数数量；未执行 QML/shader，未复制其代码或资源。证据与散列见 `analysis/11-vofa-image-effects-2026-10-22.md`。
- 新增 10 种自主 CPU 效果：海报化、卡通、泛光、暗角、扫描线、波普、移轴模糊、色相隔离、冲击波、摆动；效果总数 20。每种一项有界参数，沿用 schema4 的整数参数范围；算法为自有近似，不是 shader 等效。
- 新增测试覆盖 10 种新效果的参数上下限、尺寸和输出差异，以及色相参数越界拒绝；完整套件 **617 passed in 23.07s**。

## 2026-10-22 终端与姿态模型移植

- 终端显示从 `QPlainTextEdit` 改为自有 `TerminalView`（`QTextEdit` 子类），文本模式支持 ANSI SGR 颜色、粗体、下划线、256 色和真彩；其他 CSI/OSC 序列移除，跨分片转义有界缓存。未实现光标移动/清屏/回车覆盖，不是完整 VT100。
- 姿态控件新增本地 STL（ASCII/二进制）与 OBJ 线框模型：单文件 ≤4 MiB、≤4000 面，按包围盒居中归一化，读取失败回退立方体；模型路径可通过控件表单设置并随工作区保存。
- 界面字体使用用户提供的苹方 SC，三个字重已放入 `assets/fonts/` 并随 EXE 打包；SF Pro 仍只在系统已安装时优先。
- 完整套件 **617 passed in 23.07s**。

## 2026-10-22 原版上下文导入与串口复测

- 新增只读 VOFA+ 1.3.10 上下文导入（`vnumber 100`）：读取 `vofa+.config.json`、`vofa+.tabviews.json`、`vofa+.cmds.json`，映射连接类型/端口/波特率/数据位/校验/停止位、引擎、Δt、缓冲、通道名称与显隐、通道比例/偏移/精度、文本编码、发送模式；含原生卡页/控件或命令组时明确拒绝。实机上下文成功导入到离屏 GUI。
- COM7 STM32G431 DAPLink 串口 9600–2000000 共 10 档只读监听，每档 2 秒：全部 0 字节，当前固件没有可解析输出。
- 原版 VOFA+ 仍处于 `未授权`，关闭授权提示后主窗口对 PostMessage/SendMessage 合成鼠标消息无响应；未做绕过，因此原版 GUI 的逐效果像素对照仍未完成。
- 改用原版 shader 离屏渲染做像素对照：PySide6 + 桌面 OpenGL 编译 `plugins/widgets/image/shaders/*.fsh`，与自有 CPU 效果同尺寸比较。threshold 完全一致（MAD 0），shockwave/tilt_shift/sharpen 接近，glow/edges/warhol/posterize 等仍有明显差异；详见 `analysis/12-vofa-shader-pixel-compare-2026-10-22.md`。
- 插入 STM32F103C8T6 后端口枚举仍只有 COM8 CH340、COM7 DAPLink 和蓝牙串口，没有新增串口；COM7 10 档波特率仍全部 0 字节，无法作为吞吐数据源。
- 完整套件 **617 passed in 23.07s**。

## 验证状态

**2026-10-22 续接已执行回归：617 passed in 23.07s。** 另完成 COM8 串口打开/发送/2400 Bootloader RX/关闭验收、本机 TCP 60 秒吞吐（约1971样本/s、84.8 KB/s、缓冲5000、RSS稳定）和最新 onefile EXE 启动/数据页验收，详见 `validation-2026-10-22-v040-acceptance.md`。串口连续 RX 吞吐仍需 TX/RX 回环或第二个真实数据源；更长时间/高码率/断线恢复未验证。

原版 VOFA+ 1.3.10 已启动并完成默认界面截图对照；UI Automation 显示 `未授权` 节点，未绕过授权。逐效果对照改用原 shader 离屏渲染：threshold 等价，sharpen/tilt_shift/shockwave 接近，其余仍有明显差异（见 `analysis/12-vofa-shader-pixel-compare-2026-10-22.md`）。COM8 115200 收发、2400 Bootloader RX、COM7 DAPLink 只读监听均通过，详见 `validation-2026-10-22-render-serial.md`。

历史v0.3.0的558 passed / 4 failed及Claude A2不作为v0.4.0通过数。后续仍需补无硬件GUI/EXE启动、自由布局、异常JSON、图像格式/内存、FFT基准、录制回放和线程退出的人工验收，而不是先扩大芯片范围。

## 接续文档

README、feature-status-0.4.0、VOFA_WORKBENCH_GUIDE、architecture、safety、CLAUDE.md、根接续方案，以及`docs/ai_context`均更新当前范围/源码与未测试边界。`NEXT_VOFA_LIVE_IMAGE.md` 的实时图像卡片已在续接中完成；旧版报告保留，不改写历史通过数。

本轮按05:53:44–06:53:44 UTC的一小时窗口结束并停止；此后不自动启动下一项功能或验收。最终源码/帮助文件冻结后已重新生成两种包，收尾仅更新交付记录，不修改随包功能。
