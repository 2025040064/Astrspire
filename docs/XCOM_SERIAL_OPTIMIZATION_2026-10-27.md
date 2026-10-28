# Astrspire 串口助手对照优化

日期：2026-10-27。范围以用户最新要求“利用XCOM优化Astrspire”为准；不继续刷板，不扩展STC或VOFA功能。

## 对照依据与实际修改

XCOM截图中可确认端口、115200、8N1、DTR/RTS开关、发送新行和S/R计数。截图S:22/R:0，没有收到板端回复，不能当作成功收发基线。REA托管分析仅发现SerialPort相关引用，元数据不完整；没有复制其代码或资源。

现有Astrspire已有文本/HEX、编码与换行、周期/文件发送、RX/TX计数、日志、COM互斥，因此保留这些链路，不重复实现。

新增：

- 数据页“串口助手 · 常用配置”：手动点击填写115200/8N1、RawData、UTF-8文本显示/发送、CRLF、DTR/RTS关闭。保留发送文本，不选择COM、不勾选连接确认、不连接、不发送。
- 连接前DTR/RTS独立选择；连接期间锁定。监视链路默认关闭这两条控制线。先设置请求状态，再打开端口，避免由本程序先使用pySerial默认置位状态；不保证驱动或硬件不存在电平短脉冲。
- 控制线选项不加入schema7；加载工程重置关闭，其余旧工程配置照常恢复。
- 取消与关闭共用句柄锁，修复实板日志出现的Windows OVERLAPPED取消/关闭竞争。阻塞读写不持有该锁。

STC调用不传控制线选项，保留原先打开方式及默认状态。没有改变擦写协议、选项字节、EEPROM或供电控制。

## 测试证据

全部证据位于`build-preview/runtime-g431-uart-20261027/`。

- 先加失败测试：`xcom-red.xml`，16失败、1通过；缺少控制线接口、GUI入口，且确定复现取消/关闭竞争。
- 实现后相关测试：`xcom-green.xml`，88通过/5.17s。
- 补充GUI回归：第一次19通过、1个收尾错误，原因为测试替身缺少stop方法，不是板卡或真实串口错误。补齐替身后`xcom-extra2.xml`为19通过/2.15s。
- 完整离线回归：`xcom-full.xml`，**821通过/49.40s**。假串口覆盖打开前控制线顺序、占用后清理、旧STC打开语义、关闭竞争；GUI覆盖不自动连接/发送、参数锁定、启动快照、载入重置及CRLF发送完成计数。
- 原生Qt界面截图：`xcom-preview/1920x1080.png`、`1280x800.png`、`1020x680.png`；三个尺寸均成功生成，新增控件可见，未连接或发送，退出后测量/投影线程关闭。已人工查看1280与最小尺寸截图。此项为自有GUI渲染，不是板端验收。

复跑命令（项目根目录）：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
& .\.venv\Scripts\python.exe -B -m pytest -q --tb=short --basetemp build-preview/runtime-g431-uart-20261027/xcom-pytest-rerun
```

本目录没有Git元数据，不能提供git diff；修改按文件和测试报告留存。检索时曾使用无效Windows通配路径及不存在的检查脚本名，随后改用实际文件与入口；不将这些失败当作检查通过。

## 未取得的证据

REA动态跟踪因Node Frida绑定缺失未创建会话。使用现有Python Frida只读附加XCOM后，180秒到期自动卸载，只记录ready；用户发送操作未被窗口覆盖，没有得到DCB或TX/RX证据。没有修补第三方程序、改变API结果或绕过软件保护。

之前的只读寄存器检查没有输出具体数值，不据此判断UART配置。当前板卡已恢复此前128KiB完整备份，本任务不再操作固件。

本轮没有真实串口收发验收，未证明DTR/RTS是此前无回复的原因；软件优化和离线通过不能写成G431通信成功。

## 使用

打开独立的`Astrspire-v0.6.0-dev3-serial-assistant.exe`，进入数据页，点“串口助手 · 常用配置”，选择端口，再显式连接。XCOM和Astrspire不能同时占用同一个COM口，测试Astrspire前先在XCOM点“关闭串口”。发送计数仍只表示已交串口驱动；只有RX增加并收到预期应答才是实际回包证据。

## 交付收口

- 单文件：`dist/Astrspire-v0.6.0-dev3-serial-assistant.exe`，85,563,124字节，SHA256 `2A6BA647801D656ECCB1CB0F4DB345D8BF28FD67189720C71EF4D8A56423D11A`。
- 目录版：`dist/Astrspire-v0.6.0-dev3-serial-assistant/`，必须整体使用；内部主EXE的SHA256 `0093141B5C683E764DFB63F6266D73C14652D078597CDA6DDE577E6F0B6BDD6F`。
- 源码：`dist/Astrspire-v0.6.0-dev3-serial-assistant-source.zip`，由固定白名单生成，附逐文件SHA256清单，校验ZIP CRC与归档内容哈希。不打包板卡Flash/UID备份或XCOM程序。
- 构建日志为`xcom-build-onefile.log`、`xcom-build-onedir.log`。可选`pyqtgraph.opengl`因未装OpenGL未收集；本次串口助手与二维绘图不使用该模块。另有忽略macOS串口系统库的提示。不自动安装新依赖。
- 两种EXE均从项目F盘空工作目录实际打开，标题正确、窗口响应，随后正常关闭；证据在`build-preview/runtime-v060-dev3/`对应`-serial-assistant-*-startup.json`。这不是完整打包界面或板端收发验收。
- 原dev3单文件SHA256仍为`B95838A1C0328286D184616DC750E346AF8B85B757E43251B823DCDD054EDC19`；原uart-test仍为`DF413B4CF4933E25038D56DAF8C14B8E92D6194128007B5C3BA7AA88A9E2D561`，均未覆盖。
