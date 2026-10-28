# v0.5.0 软件与产物验收 · 2026-10-23

## 结果

- 初始基线：617 passed /29.69s，`build-preview/runtime-v050/baseline.xml`；旧604项报告保留，不改写。
- 交付源码：701 passed /23.27s，`build-preview/runtime-v050/release.xml`、`release.log`，失败0。命令：`.venv\Scripts\python.exe -m pytest -q --basetemp=build-preview/runtime-v050/release-tmp --junitxml=build-preview/runtime-v050/release.xml`，QT_QPA_PLATFORM=offscreen，TEMP/TMP及缓存在F盘。
- 缺陷均先复现：100000→5000历史缩短、CSV来源时间丢失、批次截断、记录失败、页面重建Qt异常、停止Demo按钮被重启、累计帧轴重置、扩大显示缓存未重建原始历史。red/green日志分别保留，不能把早期失败报告当最新状态。
- 本机TCP全MainWindow验收：约60秒、2500帧/秒、JustFloat共150000帧/2400000字节，序号丢失/重复/乱序均0；最大256事件/批，原始保留100000、淘汰50000；零应用TX，暂停显示不停止采集，重建换算保留原值及100000帧。
- 证据：`build-preview/runtime-v050/loopback-release/result.json`（含源码指纹、RSS、检查项），同目录PNG截图。旧两轮工具断言失配及时间轴问题记录保留在`loopback-60s*`，不覆盖为成功。

## 打包与启动

两种构建均退出0，日志`build-onedir-final.log`、`build-onefile.log`。保留旧版本。

| 产物 | 大小 | SHA-256 |
|---|---|---|
| `dist/Astrspire-v0.5.0.exe` | 68836274字节 | `D3A812C67407BD4EA64FDFFEC2A7465C6C563792866D7613781A0AFFC6E7541D` |
| `dist/Astrspire-v0.5.0/Astrspire-v0.5.0.exe` | 2661646字节 | `7801DDAAE2D4E00701C65E31EA2A42A09738A7852429D4072872F310D174F4D0` |

目录版需完整`_internal`，不能只复制启动EXE；启动验收前目录总154997491字节（运行日志会增加总量）。两种包从F盘空工作目录普通窗口启动，均找到标题`Astrspire v0.5.0`且Responding=True；自然关闭后无残留同名进程。隐藏启动因窗口不可见未取得MainWindowTitle，未算通过；仅终止了本轮启动的三个隐藏测试进程，再以普通窗口复测成功。最后无进程的Get-Process导致shell返回1，这是“已无残留”，不是构建或程序崩溃。

## 边界与未完成

- 本机回环不是板卡结果，见`hardware-v050.md`。本轮不改板卡Flash，STM32持续收发、三板下载/启动/恢复验证未完成。
- Qt离屏GUI自动化和源码截图，不等于每个原生Windows交互/DPI都已人工验收；EXE启动只验证主窗口，未在两种包内重新执行全部701项测试。
- 自定义多波形卡目前按完整快照重建；100000点多卡高刷新率性能未专项通过。原生非空卡页/命令组结构仍无证据，明确不支持；滤镜像素等效、完整VT100/三维/Qt5插件不在本版。
- onefile使用F盘解包策略，其他无F盘电脑未验证。工程无Git仓库，`git status`失败；交付用源码SHA清单定位，不宣称存在提交记录。
- 构建、测试、窗口启动、硬件分别记录，不能互相代替。
