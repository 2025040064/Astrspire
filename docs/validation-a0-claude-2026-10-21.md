# A0 接续基线验证记录（Claude Code）

日期：2026-10-21。执行者：Claude Code。工程：`F:\A_LIU_Astrspire\VS\51烧录`。代码版本：0.2.0（`pyproject.toml`）。

本记录只覆盖 A0 基线，不改变任何源码、注册表或门禁。真实串口未打开，未发送硬件命令，未擦写。

## 已阅读

`CLAUDE.md`、`docs/ai_context/CONTEXT_PACK.md`、`docs/ai_context/SOURCE_INDEX.md`、
`docs/ai_context/PROJECT_STATE.md`、`docs/ai_context/OPEN_QUESTIONS.md`、
`docs/feature-status-0.2.0.md`、`docs/validation-0.2.0.md`、`docs/safety.md`、
根目录 `CLAUDE_CODE_IMPLEMENTATION_PLAN.md`。

REA 未使用：本轮没有需要新证据的问题，未读取或改动 REA 配置。

## 工作区状态

- 工程目录**不是 git 仓库**（无 `.git`），无法用 diff 检查历史改动；本轮按文件时间戳与内容确认。
- 本轮新增仅限 `build-preview/` 下的诊断产物，源码、测试、文档、EXE 未改动（仅新增本记录及状态索引一行）。
- 已核对 `dist/Astrspire-v0.2.0.exe` SHA-256 =
  `971283B8F77377523DFF22BFB2AD3E9390966D68C7799401FFF52AC6A039B09A`，与 `validation-0.2.0.md` 一致。

## 离线测试

首次按原命令直接运行时出现 55 个 `PermissionError [WinError 5]`：

- `C:\Users\Legion\AppData\Local\Temp\pytest-of-Legion` 与工程 `.pytest_cache` 对当前账户
  `Legion` 拒绝访问；`icacls` 也无法读取其 ACL（Access is denied）。
- 这是本机目录权限异常，不是测试失败。未尝试修改系统 ACL。

改用工程内可写临时目录并关闭 pytest 缓存后复跑：

```powershell
$env:TEMP="$PWD\build-preview\pytest-tmp"; $env:TMP=$env:TEMP
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --junitxml=build-preview/test-results-a0-2026-10-21.xml
```

结果：**228 passed in 7.89s**，0 failed、0 skipped。JUnit：`build-preview/test-results-a0-2026-10-21.xml`。

- 本机存在原始私有官方记录，逐帧对照测试实际执行，因此本轮无 skip。
- 未安装或升级依赖，使用既有 `.venv`（Python 3.12.13）。

## GUI 基线检查

使用源码（非 EXE）、Qt offscreen 平台、仅合成演示与 `examples/led.hex`，脚本
`build-preview/a0_gui_check.py`，输出 `build-preview/a0-gui-check.log`。结果：**通过**。

| 检查项 | 实测 |
|---|---|
| 最小窗口 | 1020×680，ISP 侧栏垂直滚动范围 463（可滚动）；横向滚动策略 `ScrollBarAlwaysOff` |
| 数据页侧栏 | 垂直滚动范围 100 |
| 发送输入框 | `isEnabled=True`、`isReadOnly=False`，写入文本可回读 |
| 真实模式门禁 | 按钮禁用，文案“真实下载未开放”；`HARDWARE_DIALECTS` 为空 |
| 模拟流程 | 可启动，取消后 `_active=False` 且 QThread 引用清空（工作线程退出） |
| 合成演示 | 选中“演示数据”后启动，5 个采样；停止后定时器与演示状态均结束 |
| 串口打开尝试 | 0（全程以守卫替换 `serial.Serial`，任何打开都会抛错） |

说明：

- 启动时 `refresh_ports()` 只做只读枚举（`serial.tools.list_ports`），不打开端口。
- 独立脚本中用 `QTest.qWait` 等待会话线程退出时，本机 PySide6 6.12 + 该界面组合会出现
  事件循环不再返回（faulthandler 显示主线程停在 qWait）；改用 `processEvents()` 轮询即正常。
  同一取消路径在 pytest-qt 的 `waitUntil` 下通过（228 项测试含该用例），故判断为
  测试工具/平台组合问题，未定位到应用代码死锁；列入剩余风险，后续复测。

## 未运行项

- 未打开原生窗口逐控件人工点击；GUI 检查为 offscreen 自动化。
- 未打开真实 COM、未做只读识别、未下载、未擦写。
- 未重新执行 REA 分析；未验证 EXE 启动（本轮以源码为准，EXE 仅核对哈希）。
- 未做不同 DPI、真实连续收发与吞吐测试。

## 结论与下一步

A0 完成条件满足：有新测试结果与日期、有入口与运行状态记录、无私有记录导致的 skip、
全程无隐式串口连接/发送/配置变更。

下一步为 **A1 只读识别实板对照**，需要本次会话的明确设备操作授权、备用板与端口选择；
在用户确认前不进行任何设备操作。
