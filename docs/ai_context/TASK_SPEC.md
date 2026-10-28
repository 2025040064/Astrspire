# Task Spec · v0.5.0 / VOFA 主功能闭环

当前任务和准入条件以 `../TASK_V050.md` 为准，架构见 `../architecture.md`。2026-10-23 用户明确批准实施、离线测试与独立三板验收，取代下面的旧“无需校验/一小时”范围。STC 用户确认没有恢复 HEX，因此只识别、不擦写。软件、打包和硬件结果分别回填当前 v0.5.0 文档。

## v0.4.0 历史任务（不作为当前执行指令）

## Current Task

用户于2026-10-22将范围收紧为“先不搞全芯片，先完成完整VOFA功能”。早前执行受额度限制中断，不把等待时间算成持续开发；用户接续后重新要求工作1小时，当前窗口05:53:44–06:53:44 UTC（本地13:53:44–14:53:44）。只推进数据工作台，不改STC真实写入范围，不重新操作板卡。

## This Session Will Do

- REA只读追踪指定VOFA 1.3.10的Demo、图像和可视控件资源；观察、推断、未知分开。
- 自主实现图像显示/目录回放和有证据的图片帧；扩展slider/dial/toggle/delay/joystick/light/cube/image/circular gauge。
- 接上完整工作区（命令、控件、解析扩展、主题）、绘图参数与终端、原始录制回放；新格式明确标为自有格式。
- 将本轮可交付源码打包为独立v0.4.0 EXE，旧版保留。

## This Session Will Not Do

不改Unimate或STC下载核，不安装依赖，不打开硬件/供电/选项，不解锁其他芯片，不绕过VOFA授权，不复制专有资源，不声称原生DLL/QML插件或原生工程格式兼容。

## Files To Read First

PROGRESS_2026-10-21、A2报告、当前gui/vofa模块、SOURCE_INDEX、安全说明。

## Files Not To Touch

用户原始AiCube/VOFA可执行文件、私有历史抓包、Unimate源码、全局客户端/工具链配置。

## Uncertainties

本轮已补FW/JF图像头和编码格式25–34静态证据；raw Qt5/Qt6运行对应、原生工程、完整插件ABI和全部可达菜单仍未知或未确认。REA未建立字段不编造；标准JPEG/PNG和声明式扩展不等于完整原生兼容。只读QML出现某种控件不证明其运行路径已验收。

## Verification Method

继续遵守用户“不再校验”；不运行pytest、GUI验收或硬件检查。源码实现与打包不是功能验收。历史558 passed/4 failed只属于此前版本，不能作为v0.4.0通过数。REA静态分析是本轮功能取证而非运行验收。所有可控新写入放F盘工程build-preview/runtime-v040、build-clean、dist、captures/private；复用既有工具，不下载依赖。

## Suggested Next Step

一小时结束按真实源码和证据更新覆盖表，交付EXE并如实列剩余项；不把时间到期写成“所有VOFA功能已完成”。

## Implemented Source / Not Verified

已写入多格式图像/封装/滤镜、原始JSONL与矩阵回放、13种控件/属性表单/自由布局/撤销重做、主题/声明式引擎、统计FFT/通道显示换算、完整自有schema4工作区、F1内置帮助及两份合成回归定义。所有行为未运行验收，测试`not actually run`。REA目标静态证据已保存关闭。最终包状态与时间集中记录到`../PROGRESS_2026-10-22-VOFA.md`。

仍未完成native工程/Qt5 DLL ABI、完整3D模型、全部效果参数/ANSI语义、完整VOFA运行等效。STC范围不变。

## Delivery

最终onefile/onedir均已构建退出0，精确大小/UTC时间见本轮进度。小时窗口结束后停止本轮；下一项图像卡片实时索引接入写在`../NEXT_VOFA_LIVE_IMAGE.md`，不冒充已经执行。
