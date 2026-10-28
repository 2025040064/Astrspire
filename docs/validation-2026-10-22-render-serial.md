# 原版 VOFA+ 渲染对照与实板串口回归

日期：2026-10-22。工程：`F:\A_LIU_Astrspire\VS\51烧录`。对象：本机 VOFA+ 1.3.10（`E:\app\x64\vofa+.exe`）与本项目 Astrspire v0.4.0。

## 原版启动与渲染观察

- 已启动原版，窗口标题 `VOFA+ 1.3.10`，进程响应正常；EXE 身份沿用历史记录 `D4E53EA60484293B1A701EBE7A4D2DB38FEE267EBEAB24746565F6B14EC0C370`。
- 截图：`build-preview/vofa-window-20261022.png`，SHA-256 `F7A0E6A353AF5E2A15349FCC2F12765226C2DE0C790E8143E9310297A3441AB0`。
- 默认界面：左侧“协议与连接”，数据引擎 FireWater、数据接口 Demo 数据；中央绘图区显示 VOFA 标志；右侧实时值 I0–I4；底部 Δt 1 ms、缓冲区 50000、缓冲进度 871/50000、HEX/UTF-8/收发控制。
- Windows UI Automation 枚举到该进程存在名为 `未授权` 的窗口节点。**没有绕过授权、没有修改授权状态、没有注入或调试原程序**。

### 对照结论（视觉层，不是像素级）

- 结构对应：左侧连接/引擎配置，中间波形与日志，右侧实时通道数值；与原版主界面布局方向一致。
- 本项目截图：`build-preview/astrspire-data-20261022.png`，SHA-256 `83BF2BB635611F83744F7ADC41752FD4E0EFA5DA7030BE8EAF9D3EF3C60972B5`。
- 本项目为浅蓝白主题、中文控件；原版为浅灰白、紧凑控件。字体、间距、图标和边框均为自有实现，不复制原版资源。
- 20 种图像效果的自有渲染总览：`build-preview/astrspire-effects-montage-20261022.png`，SHA-256 `6C68E4AFB6C82D5942673301BFF2BFB2535F600C78F6F1F407495366AC10A196`。

### 未完成的对照

- 原版处于 `未授权` 状态，且 Qt Quick 控件未通过 UI Automation 暴露可调用按钮；没有在未授权状态下强行操作或绕过。
- **没有做逐效果、逐像素的原版/自有渲染对比**，也没有把原版截图当作算法 golden。当前结论仅为默认界面结构和风格对照。

## 实板串口回归

### COM8 / STC89C52RC / CH340

1. 115200 / 8N1：连接成功；发送 HEX `AA 55 00 FF`，`tx_bytes=4`、`rx_bytes=1`（噪声/回显）、`samples=0`；停止后线程退出，端口租约 `None`。日志目录 `build-preview/serial-accept-logs`。
2. 2400 / 8N1：周期 200 ms 发送 `7F`，5 秒内 `tx_bytes=24`、`rx_bytes=61`；收到 STC Bootloader 状态帧，说明真实 RX 链路仍可用；停止后线程退出，租约 `None`。日志目录 `build-preview/serial-rx-probe-logs`。
3. 两次操作均未擦除、未写入芯片。2400 探测后板子可能停在 Bootloader，断电重启即可恢复用户程序。

### COM7 / STM32G431RBT6 / DAPLink 串口

- 只读打开 115200 / 8N1，监听 3 秒，不发送任何字节：`tx_bytes=0`、`rx_bytes=0`、`samples=0`；停止后线程退出，租约 `None`。
- 追加波特率扫描：9600、19200、38400、57600、115200、230400、460800、921600、1500000、2000000，每档 2 秒；全部 0 字节。脚本 `build-preview/com7_stream_probe.py`。
- 日志目录 `build-preview/serial-com7-logs`；探针脚本 `build-preview/serial_com7_probe.py`。
- 结论：COM7 打开/关闭与端口租约正常；当前固件没有输出可解析数据，未做 STM32 烧录，也无法据此完成固件数据流验收。

### 串口连续吞吐

- 本轮没有可用的连续数据源：COM8 是 STC Bootloader/用户程序，COM7 各波特率均无输出，TX/RX 回环线也未确认。未执行串口连续吞吐，不能把本机 TCP 吞吐当作串口结果。

## 原版上下文导入

- 已读取本机原生上下文 `C:\Users\Legion\AppData\Local\vofa+\100\context` 的 `vofa+.config.json`、`vofa+.tabviews.json`、`vofa+.cmds.json`（vnumber 100）。
- 只读映射到本项目：连接/串口帧格式、FireWater、Δt、缓冲 50000、通道 I0–I4 名称/显隐/比例、精度 6、UTF-8 编码、发送模式；原生 tabviews 为空、cmds 为空，因此无控件/命令可导入。
- GUI 增加“导入 VOFA+ 配置”，离屏验证成功；含原生卡页/控件或命令组时拒绝导入。

## 回归与交付

- 完整套件：**617 passed in 23.07s**。
- 当前产物：onefile 68,707,844 字节，SHA-256 `3A76C595711DBC131EAD00311BFE7476C03E09CD41063F224179BA218909CCE6`；onedir 154,849,107 字节（已含原生导入）。
- 本文件记录的是渲染观察、串口打开/发送/接收/关闭和原生上下文导入；不是完整 VOFA 等效验收，也不是串口高吞吐验收。
