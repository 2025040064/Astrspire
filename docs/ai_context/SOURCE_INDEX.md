# 源码索引

## v0.4.0新增和扩展

| 文件 | 接续定位 |
|---|---|
| `vofa/image_protocol.py` | 静态观察的FW/JF图像封装、有界分片和索引；不做设备I/O |
| `vofa/image_formats.py` / `image_effects.py` | 格式分派、raw/安全SVG、10种自主效果（强度/阈值/块大小参数）；兼容假设见文档 |
| `vofa/images.py` | ImageViewWidget、标准JPEG/PNG流、目录工人ack/暂停/取消；`set_decoded_frame`复用一个共享解码结果 |
| `vofa/image_store.py` | ImageFrameStore：payload/metadata/generation、32MiB载荷+64MiB解码预算、同代次单次解码、独立页与实时卡片共享 |
| `vofa/capture_stream.py` | StreamRecorder/iter_stream_records/RawReplayWorker，RX-only回放 |
| `vofa/matrix_source.py` | 数值矩阵迭代/自然排序/逐行回放、float32可选限制 |
| `vofa/plugins.py` / `plugin_manager.py` | EngineManifest/DeclarativeDecoder/PluginRegistry、自有数据式规则管理 |
| `vofa/json_data.py` | 配置JSON大小/深度/节点/重复键/非常量限制 |
| `gui.py::show_workbench_help` | 显式F1/工具帮助，读取随包两份自主Markdown，不自动打开链接 |
| `vofa/control_editor.py` | 常用参数表单与附加JSON，不发送 |
| `vofa/controls.py` / `extra_controls.py` / `layout_handles.py` | 13控件、双旋钮、按下/松开与长按、自由移动/拉伸/取消；image卡片local/live来源；仅packet_requested接传输 |
| `vofa/channel_settings.py` | ChannelDisplay显示换算/精度/单位；原始记录不变 |
| `vofa/analysis_view.py` | 有界FFT/直方图/柱形，5Hz可见页刷新、分析CSV |
| `vofa/plot_settings.py` / `waveform.py` | 时间/XY/轴/颜色/网格/PNG、多通道游标、有界分析快照 |
| `theme_editor.py` / `appearance.py` | 五预设、自有调色板、冲突/容量限制 |
| `vofa/workspace.py` | schema4（兼容1/2/3），包含配置不包含运行任务 |
| `tests/test_vofa_v040_math.py` / `test_vofa_v040_envelopes.py` | v0.4.0合成回归；2026-10-22 已随完整套件执行 |
| `tests/test_vofa_live_image.py` | ImageFrameStore预算/代次/错误、image卡片local/live、隐藏不解码、主窗口与目录回放集成 |
| `tests/test_vofa_effect_params.py` | 滤镜强度/阈值/块大小、0强度原图、参数范围、工作区往返与旧字段补齐 |
| `examples/telemetry_matrix.csv` / `engine_temperature.json` | 自有矩阵/声明式引擎示例，构建时包含 |
| `tools/build_windows.ps1` | 当前0.4.0 onedir / -OneFile，F盘构建和onefile解包目录 |

以上路径相对`src/qx_stc_isp/`，`tests`/`examples`/`tools`除外。本轮证据：`../analysis/10-vofa-static-feature-inventory-2026-10-22.md`；当前能力/未运行状态：`../feature-status-0.4.0.md`、`../PROGRESS_2026-10-22-VOFA.md`。

## 历史基础模块索引

| 文件 | 职责/关键接口 |
|---|---|
| `src/qx_stc_isp/__main__.py` / `app_launcher.py` | 模块入口 / PyInstaller入口 |
| `src/qx_stc_isp/gui.py` | MainWindow、QThread wrapper、显式串口操作与门禁；现有文件较大，新增功能保留集成测试 |
| `src/qx_stc_isp/hex_image.py` | HexImage，严格HEX、稀疏段、地址边界 |
| `src/qx_stc_isp/firmware_files.py` | load_firmware、buffer_preview、export_firmware；HEX/BIN文件操作 |
| `src/qx_stc_isp/chip_catalog.py` / `dialects.py` | 静态目录与空硬件注册表；目录不是烧录支持表 |
| `src/qx_stc_isp/framing.py` | sum8/sum16候选纯帧与有界流式重组 |
| `src/qx_stc_isp/stc89.py` | Stc89Status、Stc89BaudPlan、命令字段/ACK、严格指纹注册表 `ACCEPTED_HARDWARE_PROFILES`（仅 F002 / 66 43 FD） |
| `src/qx_stc_isp/stc89_session.py` | Stc89ReadOnlySession / Stc89DownloadSession；后者默认关闸，擦除前核对已注册指纹、范围与 purge 能力 |
| `src/qx_stc_isp/session.py` / `simulator.py` | SessionEvent/State、纯模拟，SIM/载荷 |
| `src/qx_stc_isp/official_replay.py` | 原始记录 hash+事务预检和离线事件回放 |
| `src/qx_stc_isp/ports.py` / `port_lease.py` | 只读枚举 / 同进程独占 |
| `src/qx_stc_isp/transport.py` | PySerialTransport、取消/关闭、purge_buffers（不是flush） |
| `src/qx_stc_isp/vofa/serial_link.py` | SerialLinkWorker：有界TX队列、同线程RX/TX、精确统计 |
| `src/qx_stc_isp/vofa/transmit.py` | 文本/HEX与结束符校验，单包上限 |
| `src/qx_stc_isp/vofa/waveform.py` | WaveformWidget：显示快照、视图、通道、绘图 |
| `src/qx_stc_isp/vofa/network_link.py` | 显式 TCP 客户端/单连接服务器/UDP；有界收发、取消、统计 |
| `src/qx_stc_isp/vofa/playback.py` | 自有长格式 CSV 流式回放，暂停/倍速/循环，逐批名称快照 |
| `src/qx_stc_isp/vofa/commands.py` / `send_jobs.py` | 严格命令 JSON；用户显式启动的顺序/文件发送，入队进度、取消、断线停止 |
| `src/qx_stc_isp/vofa/controls.py` | 数值/仪表/按钮控件，自有布局 JSON，不自动发送 |
| `src/qx_stc_isp/appearance.py` / `panels.py` / `ui_icons.py` | 明暗主题、原生可拖动分区、自绘导航图标 |
| `src/qx_stc_isp/vofa/` 其余文件 | 三数据引擎、模型、CSV、schema3工作区（兼容1/2） |
| `tools/analyze_aicube_trace.py` | 已捕获记录检查、精确golden与脱敏夹具；输出拒绝覆盖 |
| `tools/a2_f002_download.py` | A2 实验入口：默认预检查；写入需显式端口、led.hex 哈希、确认短语与已注册指纹，原始 TX/RX 写入 private |
| `tools/parse_aicube_catalog.py` | 只读解析 PE 识别表 1345 条 32 字节记录到 private JSON（字段追踪用） |
| `tools/build_windows.ps1` | v0.3.0 默认onedir，-OneFile单文件EXE；构建/缓存/解包目录 F 盘，局部环境后恢复 |
| `tests/test_stc89_session.py` / `test_official_replay.py` | 核心和官方逐帧证据回归 |
| `tests/test_gui*.py` | 门禁、交互、线程、导出/CSV异常和布局 |
| `tests/test_vofa_transmit.py` / `test_waveform_controls.py` | TX/并发取消、波形操作 |
| `tests/test_firmware_files.py` / `test_transport.py` | 文件边界/导出、purge行为 |

分析证据：`docs/analysis/05-successful-session-2026-10-21.md`、`06-stc89-offline-adapter-2026-10-21.md`、`07-stc89-core-evidence.md`、`08-aicube-gui-inventory-2026-10-21.md`、`09-catalog-field-tracing-2026-10-21.md`。最新能力表：`docs/feature-status-0.3.0.md`；当前未复测状态：`docs/PROGRESS_2026-10-22.md`。
