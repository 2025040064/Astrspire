# v0.4.0 实时调试与 EXE 验收记录

日期：2026-10-22。工程：`F:\A_LIU_Astrspire\VS\51烧录`。范围：真实 COM8 串口基础验收、本机 TCP 长时吞吐、最新 onefile EXE 启动与数据页。

## 构建

- 使用 `tools\build_windows.ps1 -OneFile` 重新构建，退出 0。
- 产物：`dist\Astrspire-v0.4.0-pre-rename.exe`，47,436,635 字节，2026-10-22 20:51:48。
- SHA-256：`010413E8EEBB8F71F8EC65325314686687106D8E264407589FE642127FD4933B`。
- 旧包已备份：`dist\Astrspire-v0.4.0-pre-live-image.exe`，SHA-256 `03DB63E390BEA80E1C67EC5A730F6A856E95407B4AE56161F9F8CEFD76F7A95F`。
- onedir 本轮未重新构建；只验证 onefile。

## 真实串口（COM8 / CH340）

> 端口分配（2026-10-22 确认）：COM8 为 STC89C52RC 烧录/识别板；COM7 为 STM32G431RBT6，仅用于数据调试，不烧录。程序对外名称已改为 Astrspire，产物名为 `Astrspire-v0.4.0`。

使用程序自身的 `SerialLinkWorker`，115200 / 8N1 / FireWater：

- 连接成功，状态“已连接 COM8；持续接收，仅发送用户明确提交的数据”。
- 发送 HEX `AA 55 00 FF`：`tx_bytes=4`，驱动完成。
- 收到 `RX 00` 1 字节（噪声/回显，不是有效数据流），`samples=0`，`image_frames=0`。
- 断开后监视线程退出，`PORT_LEASES.owner_for("COM8")` 为 `None`。
- 日志：`build-preview/serial-accept-logs/monitor-20261022-205124-802859.log`。

**限制**：COM8 上的 STC89 板没有持续数据源；115200 下只验证了打开、发送、接收原始字节和关闭。另有 2400 波特率真实 RX 探测（见下），连续 RX 吞吐仍未验证。

### TX/RX 回环尝试（未通过）

用户短接 CH340 TX/RX 后复测：

- 程序 460800、512 字节/20 ms 周期发送 60 秒：TX 62,976 字节，RX 仅 1 字节（`00`）。
- 直接用 pyserial 在 115200、9600、460800 写入 `AA 55 00 FF`，均只读到 `00`，没有回显。
- 结论：当前短接没有在 COM8 上形成有效回环，可能是接在 MCU 侧 TX/RX 而 MCU 引脚仍在驱动，或短接点不在 CH340 的 TX/RX。串口连续吞吐仍**未验证**。

需要：确认 COM8 对应的 CH340；短接 CH340 模块自身的 TX/RX，并让 MCU 的 TX/RX 与这两条线断开（或 MCU 断电且 CH340 仍供电），避免总线争用。

### 2400 真实 RX 探测

- 以 2400/8N1 连接 COM8，HEX 周期发送 `7F`（200 ms）。
- 5 秒内 TX 24 字节、RX 61 字节；日志可见状态帧片段 `B8 AC BF FB ... 76 16`，是 STC Bootloader 响应，不是软件合成数据。
- 停止后监视线程退出，租约释放。日志：`build-preview/serial-rx-probe-logs/monitor-20261022-205632-596021.log`。
- 这证明真实串口 RX 字节链路和 FireWater 监视路径可用；速率受 2400 波特率和 Bootloader 应答节奏限制，不能代表高吞吐。
- 探测后板子可能停在 Bootloader；断电重启即可恢复用户程序。

## GUI 长时吞吐（本机 TCP 回环）

本机 TCP 服务器 `127.0.0.1:19001` 发送 16 通道 FireWater 帧，目标 2000 帧/秒，GUI 使用 TCP 客户端源：

- 60.2 秒：`sample_index=118700`，`rx_bytes=5,104,100`（约 1971 样本/秒、84.8 KB/s）。
- 采样缓冲保持上限 5000 点；发送端无错误；停止后监视线程退出，端口租约空。
- 30 秒复测 RSS：启动 74.7 MiB → 10 秒 88.7 MiB → 20 秒 91.5 MiB → 30 秒 91.5 MiB，之后稳定。
- 结果：`build-preview/tcp-throughput-2026-10-22.json`、日志 `build-preview/tcp-throughput-logs/`。

**限制**：这是本机回环和软件接收吞吐，不是硬件串口吞吐；未验证更高码率、长时间 >60 秒或断线重连。

## EXE 启动与数据页

从空目录 `build-preview/exe-clean-20261022` 启动 onefile：

- onefile 父进程与解包子进程均 Responding；主窗口标题 `QX-STC ISP v0.4.0`（改名前的旧构建，文件现名 `Astrspire-v0.4.0-pre-rename.exe`）。
- 子进程工作集约 140 MB；窗口正常绘制 STC 页。
- 点击左侧数据入口后，数据页正常显示：协议与连接、波形、控件/图像/统计频谱标签、数据收发、周期发送。
- 截图：`build-preview/exe-v040-20261022.png`（STC 页）、`build-preview/exe-v040-data-20261022.png`（数据页）。
- 关闭窗口后无残留 `Astrspire-v0.4.0-pre-rename` 进程。

**限制**：未在 EXE 内逐控件点击实时图像卡片/滤镜参数；未做不同 DPI、原生窗口拖拽和长时 EXE 运行。onefile 固定 F 盘解包路径，跨机器可迁移性未验证。

## 回归

- 完整套件：**617 passed in 23.07s**，`build-preview/test-results-v040-filter-params-2026-10-22.xml`。

## 结论

本轮完成了真实串口打开/发送/接收（2400 Bootloader 状态帧）/关闭、本机 60 秒数据吞吐、最新 EXE 启动与数据页验收。**不能据此声称硬件串口持续吞吐或完整 VOFA 等效已验收**；下一步需要 TX/RX 回环或第二个真实数据源，再做连续串口吞吐和断线恢复。
