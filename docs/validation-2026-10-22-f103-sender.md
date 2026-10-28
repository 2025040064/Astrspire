# STM32F103C8T6 串口发送固件与吞吐尝试

日期：2026-10-22。用户明确授权擦除并重刷 STM32F103C8T6（ST-Link）。

## 目标与写入

- ST-Link：SN `37FF71064E57343613D01343`，FW `V2J46S7`，电压 3.28 V。
- 目标：Device ID `0x410`，STM32F101/F102/F103 Medium-density，Flash 128 KiB，Cortex-M3。
- 固件：`build-preview/f103_firmware/main.c`，裸机、HSI 8 MHz、230400 8N1，向 USART1（PA9/PA10）、USART2（PA2/PA3）、USART3（PB10/PB11）同时发送 16 通道 JustFloat（68 字节/帧，帧尾 `00 00 80 7F`）。
- 第一次写入的固件有 PA9 配置错误；经用户再次授权后重刷修复版。两次均擦除扇区 0–1、写入并校验成功，MCU 已复位运行。
- **原板固件已被覆盖，未保留原 hex；本项目无法恢复原程序。**

## 运行验证

热连接（Hot Plug）读取寄存器：

- `USART1_SR = 0x000000C0`：TXE/TC 置位。
- `USART1_BRR = 0x00000023`：约 228571 Bd（目标 230400，误差约 -0.8%）。
- `USART1_CR1 = 0x00002008`：UE + TE 已使能。
- `GPIOA_CRH = 0x888444B4`：PA9 = 0xB（复用推挽 50 MHz），PA10 = 0x4（浮空输入）。

结论：固件已运行并在 USART1 发送。

## 接收结果

- COM7（DAPLink USB 串口）230400 监听 4 秒：0 字节。
- COM8（CH340）230400 监听 4 秒：1 字节 `00`（与 STC 板噪声一致），没有 JustFloat 帧尾。
- 结论：F103 的 PA9 没有实际到达这两个 COM 口，或 TX/RX/GND 接线不成立；当前无法完成串口连续吞吐。
- 需要：确认 PA9（F103 TX）接到 USB-TTL 的 RXD、GND 共地，或确认 DAPLink VCP 的 TX/RX 是否真的接到 PA9/PA10。当前 USART2/3 也在发送，若接的是那两路也可接收。

## 最小系统板改走 USB CDC（2026-10-22）

- 用户说明 F103C8T6 是最小系统板，没有板载 USB-TTL；改为 STM32CubeF1 的 USB Device CDC 方案。
- 固件：`build-preview/f103_usb_cdc/`（HAL + USB Device Library + CDC class，HSE 8 MHz PLL 72 MHz，USB 48 MHz），已编译并写入、校验成功。
- 热连接读寄存器：RCC 已锁定 72 MHz；`USB_CNTR=0x0000BF0C`、`USB_ISTR=0`，USB 外设已初始化；PA11/PA12 为浮空输入；GPIOB ODR 中 PB14 未拉高（该板不依赖 PB14 控制上拉时无影响）。
- 写入后 PC 没有出现 `VID_0483:PID_5740` 的虚拟串口，也没有新增 COM。说明 F103 的 USB 口没有连到 PC，或板卡 USB D+/D- 未接出；当前无法验证 USB CDC 吞吐。
- 需要：用 USB 线把 F103 的 USB 口接到电脑，确认 Windows 出现 STM32 Virtual COM Port；或说明该板 USB 口是否可用。

## 文件

- `build-preview/f103_firmware/main.c`
- `build-preview/f103_firmware/f103.ld`
- `build-preview/f103_firmware/f103.hex` / `.bin` / `.elf`
- 构建工具：`D:\tools\STM32CubeCLT_1.18.0` 的 arm-none-eabi-gcc 与 STM32_Programmer_CLI 2.19.0。
