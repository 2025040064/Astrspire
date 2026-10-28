# v0.2.0 本轮验证与交付

日期：2026-10-21，Windows 11 x64。结论：离线功能和可启动的预览 EXE 已交付；**全功能/全芯片目标仍未完成，自研实机烧录未验证。**

## 环境

| 组件 | 本次实际读取版本 |
|---|---|
| Python | 3.12.13 x64 |
| PySide6 | 6.12.0 |
| pySerial | 3.5 |
| IntelHex | 2.3.0 |
| pytest / pytest-qt | 9.1.1 / 4.5.0 |
| PyInstaller | 6.22.3 |

源码/锁已标记项目版本0.2.0。本轮使用既有 `.venv`，未安装新依赖或更改系统工具链。

## 实际测试

```powershell
.venv\Scripts\python.exe -m pytest -q --junitxml=build-preview/test-results-0.2.0.xml
```

最终结果：**228 passed in 7.12s**，0失败、0跳过。JUnit XML 已生成在上述路径。原始私有官方记录本机存在，相关逐帧测试实际执行；分发环境不含该文件时会跳过一项，需如实记录。

覆盖：HEX/BIN边界与导出、严格帧、模拟/官方回放、F002参数生成和逐帧核心、仅同步只读识别、门禁、错误ACK/断线/取消/关闭失败、串口清空与TX队列、解析器、波形交互、schema2工作区、CSV异常和GUI布局。最后新增回归覆盖导出格式/后缀错配、CSV清理二次异常、超长设备描述撑宽侧栏。

全部设备操作测试使用假传输；本轮未打开真实 COM、未发送硬件命令、未擦除或写入芯片。历史官方成功会话与本轮测试的证据等级不同。

## GUI 与打包

- 源码离屏启动和两页1020×680布局检查：通过；明确加载Windows中文字体后检查了无侧栏横向溢出、无波形/提示重叠。截图在 `build-preview/monitor-split.png` 等诊断文件中；其中早期截图是修复过程，不作最终发布截图。
- 最终源码的窄窗几何由自动化GUI回归覆盖。离屏截图不是已连接设备或原生桌面人工交互证据。
- 已执行 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_windows.ps1 -OneFile`，成功生成便携EXE。
- 归档检查包含 `firmware_files`、`stc89_session`、`vofa.serial_link/transmit/waveform` 和两个HEX样例；未发现Frida、private记录或原官方程序条目。
- 从空工作目录 `build-preview/launch-empty-0.2.0` 启动EXE成功，读取到主窗口标题 `QX-STC ISP v0.2.0 · STC / STM32 数据调试 · 预览版`，进程Responding=True。onefile父/子进程ID为27992/30460，仅是本次启动记录，不可用作以后进程定位。
- 未在原生窗口逐控件点击验收、未接硬件、未做不同DPI或连续吞吐测试；这些是 **not actually run**。默认onedir构建路径本轮 **not actually run**，实际验证的是 `-OneFile`。

产物：`dist/Astrspire-v0.2.0.exe`，46,201,201字节。

SHA-256：`971283B8F77377523DFF22BFB2AD3E9390966D68C7799401FFF52AC6A039B09A`。

## 仍保留的限制

真实GUI擦写禁用；`HARDWARE_DIALECTS`为空。F002实验核默认关闸，未对实板执行；其他协议族尚无真实适配。EEPROM、选项/保护、自动供电、重复编程未启用。VOFA网络、回放、插件/控件/主题未实现，现有串口调试未实机验收。

## 收尾与下一位执行者

用户要求本轮收尾后停止，由Claude Code继续。入口是根 `CLAUDE_CODE_IMPLEMENTATION_PLAN.md`，含REA下载说明链接与分阶段验收；精简状态在 `docs/ai_context/`。REA新快照已保存并关闭会话，没有计划任务或自动续跑。下一轮先执行A0，硬件操作另行明确授权。
