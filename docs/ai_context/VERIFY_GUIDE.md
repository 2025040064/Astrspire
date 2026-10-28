# 接续验证入口

2026-10-22 当前约束：用户要求连续实施、不再校验；下列测试是以后获准复测的参考，不作为本轮执行计划。v0.4.0未运行任何pytest/GUI/设备测试，新增回归只写入源码；最新状态见 `../PROGRESS_2026-10-22-VOFA.md`。

在工程根目录使用现有环境：

```powershell
$env:TEMP = Join-Path (Get-Location) 'build-preview\runtime-v040\tmp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=build-preview/runtime-v040/pytest-next --junitxml=build-preview/runtime-v040/test-results-next.xml
.venv\Scripts\python.exe -m qx_stc_isp
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_windows.ps1 -OneFile
```

依赖缺失时参考根 README 的 uv 安装命令，先列清差异，不自动更换全局 Python。没有硬件测试授权时仅用合成演示/官方离线回放，不点击识别或连接。

硬件实验开始前读 safety 和根实施方案 A1/A2。普通测试使用假传输；不要把真实COM或自动烧录作为测试夹具副作用。Windows默认窗口使用系统中文字体；离屏截图需显式加载 `C:/Windows/Fonts/msyh.ttc` 才能可靠显示中文，不能把缺字方框当原生窗口字体结果。

历史v0.2.0结果见`../validation-0.2.0.md`，不能冒充当前版本验收。获准复测后还需覆盖：自由布局取消/发送门禁、加载冲突/深层JSON、图像类型/插件/raw颜色表/内存上限、FFT基准/暂停/显示换算、录制回放不重发TX、矩阵取消/异常和所有线程退出。
