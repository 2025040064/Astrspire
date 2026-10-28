# 任务总结 · Astrspire v0.4.0（2026-10-22）

工程：`F:\A_LIU_Astrspire\VS\51烧录`。本文汇总 2026-10-21 至 2026-10-22 的实际完成内容、验证结果、产物和未完成项。详细证据见各 `validation-*.md`、`analysis/*.md` 和 `PROGRESS_*.md`。

## 一、项目定位

- 程序对外名称：**Astrspire**（原 QX-STC ISP），版本 **0.4.0**，Python 包路径仍为 `qx_stc_isp`。
- STC 区：固件管理、只读识别、严格指纹真实烧录。
- 数据区：自主实现的 VOFA 风格工作台，用于 STM32 串口/网络数据调试；不提供 STM32 烧录。
- 唯一真实烧录目标：STC89C52RC / 芯片 ID `F002` / Bootloader `66 43 FD` / 6.6C。

## 二、里程碑

| 里程碑 | 状态 | 结果 |
|---|---|---|
| A0 基线 | 完成 | 早期 228 项测试、离屏 GUI 检查 |
| A1 只读识别 | 完成 | COM8 实板 6 次识别 F002 / 6.6C，取消与端口释放通过 |
| A2 真实下载 | 完成 | `led.hex` 实板写入成功，14 条 TX 与官方逐字节一致，GUI 一键烧录验收 |
| A3 目录/协议 | 部分 | 官方 GUI 清单、1345 条识别表字段追踪；其他协议族未实现 |
| A4 VOFA 工作台 | 部分（约 80%） | 控件、布局、图像、FFT、记录、工作区、ANSI、原生配置导入已完成；完整等效未达成 |
| A5 发布 | 部分 | onefile/onedir 已构建并启动验证；校验和/发布说明已记录 |

## 三、本轮主要改动

### 1. 改名与字体

- 窗口标题、页头、帮助标题、应用名、`pyproject.toml` 描述、EXE 名改为 Astrspire / `Astrspire-v0.4.0`。
- `dist/` 历史产物全部改为 `Astrspire-vX.Y.Z` 命名。
- 苹方 SC Regular/Medium/Bold 放入 `assets/fonts/`，启动时用 `QFontDatabase.addApplicationFont` 加载并随 EXE 打包；系统有 SF Pro 时拉丁字符优先 SF Pro。

### 2. VOFA 图像与效果

- 实时图像卡片：共享 `ImageFrameStore`，来源 local/live，索引 0–15，隐藏卡片不解码。
- 图像效果从 10 种扩到 **20 种**，每种一项有界 CPU 参数：原图、灰度、反色、黑白、像素化、模糊、锐化、边缘、浮雕、复古、海报化、卡通、泛光、暗角、扫描线、波普、移轴模糊、色相隔离、冲击波、摆动。
- 用原版 `image/shaders/*.fsh` 做离屏 OpenGL 渲染，与自有 CPU 效果逐像素比较：

| 效果 | MAD | 结论 |
|---|---:|---|
| threshold | 0.00 | 等价 |
| shockwave / tilt_shift / sharpen | 1.3–2.2 | 接近 |
| isolate / pixelate / wobble / blur | 6–11 | 近似 |
| sepia / vignette / toon / billboard | 14–19 | 有差异 |
| emboss / posterize | 54 / 65 | 差异明显 |
| warhol / glow / edges | 120 / 160 / 244 | 算法不同 |

- 证据：`docs/analysis/12-vofa-shader-pixel-compare-2026-10-22.md`，总览图 `build-preview/vofa-vs-astrspire-pixel-20261022.png`。

### 3. 姿态与终端

- 姿态控件支持 Euler/四元数、角度/弧度、偏移、比例、轴，以及本地 STL（ASCII/二进制）/OBJ 线框（≤4 MiB、≤4000 面，失败回退立方体）。
- 终端改为 `TerminalView`（QTextEdit），支持 ANSI SGR 颜色、粗体、下划线、256 色、真彩；未知 CSI/OSC 移除；未实现完整 VT100 光标/清屏/回车覆盖。

### 4. 原生 VOFA 配置导入

- 新增 `src/qx_stc_isp/vofa/native_project.py` 和“导入 VOFA+ 配置”按钮。
- 读取 `vofa+.config.json` 及同目录 `vofa+.tabviews.json`、`vofa+.cmds.json`（vnumber 100），导入：连接类型、端口、波特率、数据位、校验、停止位、引擎、Δt、缓冲、通道名称/显隐/比例/偏移/精度、编码、发送模式。
- 含原生卡页/控件或命令组时明确拒绝，不静默丢弃；实机上下文已成功导入离屏 GUI。

### 5. 其他既有能力

- FireWater / JustFloat / RawData、TCP 客户端/服务器、UDP、CSV、矩阵、图像目录、原始记录回放。
- 13 类控件、自由布局/撤销、FFT/直方图、通道属性、主题、收发 JSONL、schema4 工作区（兼容本项目 1/2/3）。

## 四、验证结果

- 完整测试：**617 passed in 23.07s**（2026-10-22，最终一次完整运行）。
- EXE 构建与启动：
  - `dist\Astrspire-v0.4.0.exe`：68,707,844 字节，SHA-256 `3A76C595711DBC131EAD00311BFE7476C03E09CD41063F224179BA218909CCE6`。
  - `dist\Astrspire-v0.4.0\`：154,849,107 字节，需完整 `_internal`。
  - 两者均启动验证，标题 `Astrspire v0.4.0`，进程响应正常。
- 串口：
  - COM8 115200：打开/发送 `AA 55 00 FF`/接收/关闭通过，TX 4、RX 1。
  - COM8 2400：周期 `7F` 5 秒，TX 24、RX 61（STC Bootloader 状态帧）。
  - COM7 DAPLink：10 档波特率只读监听全部 0 字节。
- 原版 VOFA+ 1.3.10：已启动并截图；UI Automation 显示 `未授权`，未绕过授权；逐效果对照改用原 shader 离屏渲染。

## 五、STM32F103C8T6 串口吞吐尝试

- 用户授权后通过 ST-Link（SN `37FF71064E57343613D01343`）写入测试固件；**原板固件已被覆盖，未保留原 hex**。
- 第一版 USART 固件修正 PA9 后重刷；寄存器确认 USART1 已使能发送（`SR=0xC0`、`CR1=0x2008`、`BRR=0x23`、PA9 复用推挽）。
- COM7/COM8 均未收到 JustFloat 数据，说明 PA9 未实际接到这两个口。
- 因用户说明是**最小系统板**，改用 STM32CubeF1 USB Device CDC 固件：72 MHz、USB 48 MHz、16 通道 JustFloat，已编译、写入、校验成功；热连接确认 USB 外设初始化。
- PC 未出现 `VID_0483:PID_5740` 虚拟串口，也没有新增 COM：F103 的 USB 口尚未接到电脑，或板卡 USB D+/D- 未引出。
- 记录：`docs/validation-2026-10-22-f103-sender.md`；固件：`build-preview/f103_usb_cdc/`（USB CDC）与 `build-preview/f103_firmware/`（USART）。

## 六、未完成与限制

- 逐效果 shader 完整移植未完成：threshold 等价，sharpen/tilt_shift/shockwave 接近，其余仍为自有近似。
- 原生 VOFA 卡页/控件、命令组、原生布局/主题不能导入。
- 完整 ANSI/VT100（光标、清屏、回车覆盖）未实现。
- 姿态的世界/物体轴分离、模型材质/光照、完整原生坐标约定未实现。
- 串口连续吞吐仍未验收：F103 USB CDC 需接 USB 线并出现 COM 口；USART 需正确接线。
- 原版 VOFA+ 未授权，GUI 合成输入不可用；像素对照是 shader 级，不是原窗口回放。
- STC 真实烧录仍只支持 F002 / `66 43 FD`，其他芯片未开放。
- 未做 >60 秒串口吞吐、断线重连、不同 DPI/小窗口人工验收。

## 七、下一步

1. 把 F103 USB 口接到电脑，确认出现 STM32 Virtual COM Port 后，用 Astrspire 跑持续 JustFloat 吞吐。
2. 若要继续 shader 等价：优先重写 glow/edges/warhol/posterize/emboss 为原 shader 公式，再复跑像素对照。
3. 原生工程：从已保存的项目目录继续确认 tabview/tab 结构，再扩展导入器。
4. 恢复 F103 用户固件：需要用户提供原 hex；当前板内是本项目的测试固件。
