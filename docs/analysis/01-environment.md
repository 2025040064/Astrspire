# 阶段一：环境与证据基线

记录日期：2026-10-18  
项目：Astrspire（记录时名称 QX-STC ISP）  
目标平台：Windows / STC89C52RC / CH340 / QX-MCS51；STM32G431RBT6 在 COM7，仅数据调试

## 1. 阶段结论

本阶段确认了待分析文件、REA 原生分析后端、Python 开发环境、当前串口设备和公开资料边界。没有运行 `AiCube-ISP-v6.96C.exe`，没有打开任何 COM 口，也没有向硬件发送数据。

官方公开资料可证明 STC89C52RC 支持串口 ISP 和冷启动下载，但不足以证明逐字节 UART Bootloader 协议。真实擦除和写入必须等到阶段二取得静态分析与实际通信证据后再实现。

## 2. 分析目标与完整性

| 对象 | 位置 | 大小 | SHA-256 | 结论 |
|---|---|---:|---|---|
| 原始归档 | `3-烧录软件.zip` | 16,878,851 字节 | `b4fc929ea3d8b7107b04d44944568ad2b39fe525a996df74b5af9d1b2d08a0a5` | 工作区原始文件，只读分析 |
| 目标程序 | 归档内 `3-烧录软件/AiCube-ISP-v6.96C.exe` | 9,613,312 字节 | `d23ed40a57fdcc9f399e447097cc68b8fb41472af92065ab0292b37747a2909a` | 原生 x86 PE，未签名 |

目标程序的版本资源显示产品名 `AiCube-ISP`，文件/产品版本 `0.6.96.3`，原始文件名 `AiCube-ISP.exe`。宏晶软件下载页列有 v6.96C，但目标文件没有 Authenticode 签名，因此这里只记录“名称和版本与公开下载页相符”，不宣称已完成发布者真实性认证。

## 3. REA 与原生分析后端

| 项目 | 实际观察 |
|---|---|
| REA | `6.5.0`，MCP 接口可调用；命令行程序未加入 `PATH` |
| 原生后端 | Ghidra `12.1.4`，能够完成目标程序导入与分析 |
| Java | JDK `21.0.12.1`，可供 Ghidra 使用 |
| 文件类型 | 32 位 x86 PE，非 .NET，映像基址 `0x00400000` |
| 分析规模 | 7,238 个过程、5,678 个字符串 |
| 签名状态 | `NotSigned` |

REA 证据标识：

| 证据内容 | Evidence ID |
|---|---|
| ZIP 归档检查摘要 | `ev_6d4af6b00cd70e83bac7e9b939f7f172eb727ab4ae9868ec01ac436770f20379` |
| 父归档对象检查 | `ev_17fa8e7b3904b06d0f9715fcd324478958d39d140c81c6cce463761501aaa6a3` |
| 归档文件清单 | `ev_84f3f798890fa3e2250f990bdb071f12c9993a127ea6429e06dc70d658786375` |
| 临时只读提取 | `ev_ab79dbec170cc894304d107927e6f4cecc0284afb82aef203078d1a2c1429e49` |
| Ghidra 二进制概览 | `ev_138034a1690a7ffde7c1a02a33777810839611090d6e6921d2015cd0653e7466` |
| 串口 API/字符串检索 | `ev_b10bf623af37f40ce7aa3624b7d4a2e582ef78cfd036971237f7455d5363928f` |
| PE 资源与版本信息 | `ev_b86b7470e0610ff76a64e2accc911679fda49ccb566b43082ab36c02e413d118` |

静态导入表已观察到 `CreateFileA`、`SetupComm`、`GetCommState`、`SetCommState`、`SetCommTimeouts`、`ReadFile`、`WriteFile`、`ClearCommError`、`PurgeComm`、`CancelIo`、`CloseHandle`。这能证明程序包含 Windows 串口访问能力，但不能单独证明某个协议帧或运行时调用顺序。

## 4. Python 开发环境

| 项目 | 基线 |
|---|---|
| 环境管理器 | `uv 0.11.32` |
| 固定 Python | CPython `3.12.13` x86-64 |
| 解释器位置 | `C:\Users\Legion\AppData\Roaming\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe` |
| 运行依赖 | PySide6、pySerial、IntelHex |
| 开发依赖 | pytest、pytest-qt、PyInstaller |
| 锁文件 | 根目录 `uv.lock`，由 `uv lock` 生成 |

项目不依赖 WindowsApps 的 `python` 别名；所有开发、测试和打包命令均通过 `uv run` 执行。

本次 `uv sync --all-groups` 的锁定/安装结果：PySide6 `6.12.0`、pySerial `3.5`、IntelHex `2.3.0`、pytest `9.1.1`、pytest-qt `4.5.0`、PyInstaller `6.22.3`。六个包均已从项目 `.venv` 成功导入或调用，`uv lock --check` 通过。

## 5. 当前串口与驱动状态

只读枚举结果为 `COM3`、`COM4`、`COM5`、`COM6`，均显示为蓝牙标准串口。当前未检测到 CH340/CH341 对应设备，因此没有自动选择任何端口。

系统中存在 WCH 驱动：

- INF：`oem167.inf` / `ch341ser.inf`
- 提供者：`wch.cn`
- 驱动版本：`3.9.2024.9`
- 驱动文件：`CH341S64.SYS`，文件版本 `3.90`

“驱动已安装”不等于“CH340 当前已连接”。实机测试前必须重新读取端口的 VID、PID、序列号和设备名称；多个 CH340 候选存在时不得自动替用户选择。

## 6. 归档内历史记录与样例

归档内四份 `stcisp_log` 共 307 条记录：306 条包含同一 `MCUID=F002C61B19DF76` 且 `ISPRESULT=0`；另有一条缺少 MCUID 且 `ISPRESULT=3`，11 秒后的重试记录为 `0`。将 `0` 解释为成功、`3` 解释为失败只是结合上下文的推断，不是已确认的官方错误码定义。

这些日志没有 RX/TX 原始字节、COM 口、波特率或 Bootloader 版本，不能作为 UART 协议抓包。教程中的 `AI8h USB CDC` 属于另一种 USB 接口描述，不能覆盖本项目声明的 CH340 硬件环境。

固定回归样例：

| 文件 | SHA-256 | 数据字节数 | 地址范围 | Intel HEX 行校验 |
|---|---|---:|---|---|
| `flashled.hex` | `c24a16351e421d545f3a5d3189cf3706f1c3e683610072f568bea7a26dd198c2` | 94 | `0x0000–0x005D` | 通过 |
| `led/Objects/led.hex` | `afaa00428d08cc2a97f162ec0f71708a8301cd6f26be31212b3ded9da56c6f70` | 21 | `0x0000–0x0014` | 通过 |

历史日志中的 `CHECKSUM` 与 Intel HEX 数据字节的简单加和相符；它不能证明 ISP 帧校验算法或校验覆盖范围。

## 7. 公开资料与协议边界

### 官方资料

- [宏晶软件下载页](https://stcmicro.com/rjxz.html)
- [STC89C52RC 产品资料](https://www.stcmicro.com/STC/STC89C52RC.html)
- [STC 工具手册](https://www.stcmicro.com/pdf/stc-tool-en.pdf)

官方资料确认 STC89C52RC 具有 8 KiB 程序 Flash、串口 ISP 能力和冷启动下载流程。当前没有找到宏晶公开发布的、足以独立实现该芯片外部 UART BSL 的逐字节协议说明。

### 外部逆向参考

- [stcgal 项目](https://github.com/grigorig/stcgal)
- [stcgal 协议实现](https://github.com/grigorig/stcgal/blob/master/stcgal/protocols.py)
- [stc89 逆向记录](https://github.com/grigorig/stcgal/blob/master/doc/reverse-engineering/stc89c52rc.txt)
- [stc89a 逆向记录](https://github.com/grigorig/stcgal/blob/master/doc/reverse-engineering/stc89a-c52rc.txt)

外部参考表明至少存在 `stc89` 与 `stc89a` 两种不兼容 Bootloader 方言。该信息用于规划取证范围，不能替代本开发板、本芯片和本 Bootloader 版本的实际通信证据。

## 8. 证据分级

| 级别 | 本阶段内容 |
|---|---|
| 静态观察 | PE 类型、版本资源、导入函数、字符串、归档清单、HEX 内容与校验 |
| 实际抓包 | 无；尚未运行官方程序或打开串口 |
| 外部参考 | 官方产品/工具资料，stcgal 代码与逆向记录 |
| 推断 | 历史日志中 `ISPRESULT=0/3` 的语义；版本名称与公开下载页的对应关系 |
| 未知 | 实机 Bootloader 方言、状态包、芯片 ID 映射、波特率协商、命令码、帧长度、字节序、帧校验范围、写入 ACK、结束/复位序列 |

## 9. 阶段二准入与停止条件

下一阶段只进行协议分析和只读取证：

1. 从串口 API 调用链定位初始化、同步、识别、协商、擦除、写块、确认和关闭逻辑。
2. 建立逐字段协议证据表，明确区分静态观察、抓包、外部参考、推断和未知。
3. 在用户连接备用开发板后，先重新枚举 CH340 的 VID/PID、序列号和端口名称。
4. 用户明确开始取证后，才在临时副本上运行官方程序并只读记录串口 API 参数与缓冲区；不改变返回值、不绕过保护。
5. 若只读跟踪被保护机制阻止，停止该路径，改用 USBPcap 或逻辑分析仪。

在方言、芯片 ID、帧格式、校验、写入 ACK 和结束序列全部确认前，不编写或启用真实擦除/写入逻辑。

## 10. 本阶段验证记录

| 检查 | 结果 |
|---|---|
| 原始 ZIP SHA-256 复核 | 通过，与基线值一致 |
| `uv lock` | 通过，解析 24 个包 |
| `uv sync --all-groups` | 通过，创建 `.venv` 并安装 22 个包 |
| Python 版本 | `3.12.13` |
| 运行/开发依赖导入 | 通过 |
| pytest 可调用 | `9.1.1` |
| PyInstaller 可调用 | `6.22.3` |
| `uv lock --check` | 通过 |
| 当前串口只读枚举 | 4 个蓝牙串口；0 个可确认的 CH340 |

本阶段尚未建立源代码测试集，因此这里只验证环境，不把“pytest 可调用”表述为功能测试通过。
