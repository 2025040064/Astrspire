# A2 F002 最小真实下载与官方对照记录（Claude Code）

日期：2026-10-21。执行者：Claude Code。授权：用户选择“授权 A2 最小真实下载”，并确认备用 STC89C52RC 板、可擦除现有程序、官方 AiCube 可恢复。

结论：**F002 / `66 43 FD`（6.6C）这一个严格指纹已完成实板写入、断电重启运行和官方工具同板同文件对照。GUI 已加入真实烧录入口，只对该指纹开放；其他型号和协议族继续锁定。**

> 后续变更（2026-10-21）：用户明确要求删除风险勾选和擦除确认弹窗，真实烧录现为一键启动；本文下面关于“擦除确认勾选/二次对话框”的描述保留为 A2 当时的实现记录，当前行为以 `docs/safety.md` 和源码为准。

本记录不把块 ACK 当作全片回读，也不把识别成功当作烧录成功。

## 准备与预检查

- 端口：COM8，USB-SERIAL CH340，VID:PID `1A86:7523`（用户重新选定；COM3–6 为蓝牙串行，COM7 为 DAPLink）。
- 固件：`examples/led.hex`，21 字节，SHA-256 `8DF1C8ED7E875F067D0280FED35B3B072FD06201C722CEB4288F37CAE618BA3B`。
- 恢复工具：`C:\Users\Legion\Desktop\AiCube-ISP-v6.97A.exe`，SHA-256 `6194B42B9F7812E745AB19E6F5A25B474F740F84C7AC9E7CFA940CBBBFE1CBCC` 与既有记录一致。
- 预检查（`tools/a2_f002_download.py --phase precheck`）：身份 F002 / `66 43 FD` / 6.6C，状态包 53 字节，时钟 11,030,400 Hz，参数 `8F FF FD 00 06 28 82`，写入范围 `0x0000–0x01FF`，4 个 128 字节块；未进入协商或擦除。

## 第一次实板写入（CLI 实验入口）

命令：

```powershell
.venv\Scripts\python.exe tools\a2_f002_download.py --port COM8 --phase write --confirm-erase "ERASE F002 66-43-FD"
```

结果：全部 4 个块 ACK 匹配（512/512 字节），终端状态 COMPLETE；用户断电重启后程序运行。

与官方历史抓包逐消息对照（`build-preview/a2-sequence-compare.log`）：

| 项目 | 结果 |
|---|---|
| 非同步 TX 命令 | **14/14 逐字节一致**（probe、activate、5×access、erase、4 个写块、FF、82） |
| RX 应答帧 | 13 帧中 **12 帧一致**；仅状态帧不同（时钟样本 2667 vs 官方 2681，及未知字段） |
| 擦除 ACK | 一致 |
| 写块 ACK | 4 个一致 |

发现的保真差异：官方原始记录为 **4 次 DCB 请求 + 4 次 PURGECOMM**（每次改波特率都清缓冲）；当时实现只做 2 次协议相关 purge（两次切 115200）。

## 对齐与 GUI 入口

1. 下载核改为：串口打开后清空一次，之后每次重配置波特率都清空；实板日志出现 4 次“已清空收发缓冲区”，与官方 4 次 DCB/purge 对齐。
2. 新增严格指纹注册表 `ACCEPTED_HARDWARE_PROFILES`（仅 F002 / `66 43 FD`），通用 `HARDWARE_DIALECTS` 仍为空，未把所有 STC 型号列为支持。
3. 下载核在擦除前同时校验：芯片 ID、Bootloader 原始字段、已注册指纹、HEX 范围、purge 能力。
4. GUI 新增：
   - 真实模式下“开始真实烧录…”按钮，仅在型号为 STC89C52RC/LE52RC、已选串口、已载入 HEX 且勾选“我确认真实烧录会擦除芯片现有程序，并已准备恢复手段”时可用；
   - 二次确认对话框，列出端口、型号、固件路径、擦写范围，默认按钮为 No；
   - 结束/取消/失败后自动取消勾选，不自动重试；
   - 只读识别入口保持不变。
5. 测试：新增严格指纹、purge 次数、GUI 门禁/二次确认/端口占用用例；完整套件 **232 passed（8.30–8.31s）**，JUnit `build-preview/test-results-a2-final-2026-10-21.xml`。

## GUI 实板验收

日志：`captures/private/a2-com8-20261021-gui-write.log`（18:53:41–44.688）。

- 识别 F002 / 66 43 FD / 6.6C，时钟 11,030,400 Hz；
- 打开后清空 1 次，切 115200、2400、115200 各清空 1 次，共 **4 次**；
- 擦除 ACK 正常，4 个写块 ACK 匹配；
- 结束 `FF`/`82` 发送，状态“已注册指纹事务完成，块 ACK 全部匹配”；
- 用户断电重启后确认 LED **长亮**，与官方 AiCube 同板同 `led.hex` 写入后的现象一致。

用户误选 COM4（蓝牙串行）两次，日志为 `串口 COM4 写入超时`；没有有效 7F 或擦除命令发出，未影响板子。

## 证据

| 文件 | SHA-256 |
|---|---|
| `captures/private/a2-COM8-20261021-183758.jsonl`（CLI 写入全量事件） | `612016E11A090F9F3FF28EBE0375FEB1EF8314AD48DE6E604867C577251831D3` |
| `captures/private/a2-com8-20261021-gui-write.log`（GUI 写入日志） | `1F7989F64DD517F1A59B3732146F99C362D77C486CB9FCDCC5D34026F568013C` |

诊断脚本/输出：`tools/a2_f002_download.py`、`build-preview/a2_compare_sequence.py`、`build-preview/a2-sequence-compare.log`。

## 仍然未知 / 限制

- 块 ACK 不是全片回读；协议没有已知的结束 ACK，`FF/82` 之后只能靠断电重启观察运行。
- 状态包第三字段 `FD`、准备包部分字段、特殊 `81` 应答分支仍未完全解释。
- 仅 F002 / `66 43 FD` / 6.6C / 8 KiB / 128 字节块这一指纹；其他芯片、协议族、晶振边界未验证。
- 未实现 EEPROM、选项字节、保护位、DTR 自动供电、重复编程。
- `HARDWARE_DIALECTS` 仍为空；严格指纹注册表是独立证据层，不代表通用方言。

## 下一步

- A3：官方 STC 目录/协议族/选项能力逐项取证；每个族独立适配和实板验收。
- A4：VOFA 风格数据区功能补齐。
- 任何新增型号必须先取得身份、布局、通信证据和实板验收，不能继承本指纹的偏移、块大小或结束序列。
