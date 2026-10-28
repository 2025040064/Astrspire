# v0.5.0 三板独立验收记录 · 2026-10-23

## 本轮结论

未刷写任何板卡。用户确认STM32只有ST-LINK，F103暂无数据线；用户确认STC没有恢复HEX。持续数据验收和恢复验证未完成，不以软件回环替代。缺少数据链路时没有反复刷测试固件。

| 板卡 | 当前证据 | 尚未完成/停止原因 |
|---|---|---|
| F103 | ST-LINK SN `37FF71064E57343613D01343`；Hot Plug读身份成功，Device ID `0x410`，UID原始字 `0671FF37 3634574E 43163C1A`；容量寄存器`0x1FFFF7E0=0080`，工具报告128KiB；全Flash两次读出一致 | 未枚举VID0483/PID5740 CDC，用户无数据线；未刷写，不做60秒/10分钟数据测试，不宣称设备程序启动/恢复成功 |
| G431 | COM7元数据`0D28:0204`、序列号`4T_DAPLINK_`只证明适配器存在，不证明MCU或PA9/PA10链路 | 用户确认仅ST-LINK；未重新确认目标身份/备份，未刷写，未做串口持续收发 |
| STC89 | COM8 CH340 `1A86:7523`，无序列号；未把COM号作为永久板卡身份 | 无恢复HEX，不擦写；本轮未完成冷启动识别及下载/运行对照 |

## F103备份（必须保留）

- `build-preview/runtime-v050/f103-before.bin`：131072字节，本轮开始时完整Flash。
- `build-preview/runtime-v050/f103-confirm.bin`：第二次完整读取，校对副本。
- 两份SHA-256：`D521138D6D9AEF5E36B310651379BFC55B03ED528CCB426B80F2E7B7F7EA2B79`。
- 日志：同目录`f103-identity.txt`、`f103-backup.txt`、`f103-backup-confirm.txt`。均退出0；不执行擦除、选项修改、解保护或复位命令。
- 这是当前程序备份，不能证明它是用户此前原程序，也不能恢复之前已被覆盖且无备份的程序。

枚举与工具证据：`ports.txt`、`stm32-probes.txt`、`stm32-cli-help.txt`。定位OpenOCD脚本时两次默认路径读取失败，随后实际定位到`D:\tools\openocd\openocd\scripts`，未运行OpenOCD或连接G431。

后续需要STM32 USB数据线或已确认的UART连接。若要刷写，先重新绑定探针/UID并重新备份；任何备份失败/读保护均停止。STC需当前程序对应恢复HEX及用户冷启动配合。
