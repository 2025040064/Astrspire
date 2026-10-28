# v0.5.2 验证记录 · 2026-10-23

## 范围与基线

仅核对基线、放大波形和收紧工具/终端，不实施v0.6.0数据修复、性能或命令绑定。原版软件未启动，不访问硬件。v0.5.1历史报告/源码清单和所有旧产物保留。本目录无Git仓库，不能提供提交记录。

本版修改模块仅gui、panels、波形刻度文字位置、WorkspaceSettings默认分隔比例及版本/构建/说明；采样、分析、传输、协议、控件及图像算法模块不变。旧工程的plot_splitter_sizes不迁移，schema保持6。

## 测试

- 新增8项UI回归；首次3项布局失败及刻度裁切失败日志分别为runtime-v052/red.log、red-label.log。没有延长测试超时。
- 过程中两次尺寸回归发现固定输入宽度低于最终字体的minimumSizeHint（100/112 <119）；修正为Qt原生最小尺寸策略后通过。green.log、green2.log保留失败过程。
- 相关套件首次58通过/1失败，最后新UI专项通过；交付源码全套 **728 passed /34.69s**，失败0。此前726项/39.15s是新增刻度/错误显示测试之前的中间结果，不作交付结果。
- 最终命令：`.venv\Scripts\python.exe -u -m pytest -q --basetemp=build-preview/runtime-v052/final --junitxml=build-preview/runtime-v052/final.xml`。
- 环境：现有Python3.12.13、PySide6/Qt6.12.0、pytest9.1.1；QT_QPA_PLATFORM=offscreen、TEMP/TMP及pytest输出均在F盘。未安装或下载依赖。

## UI证据

`tools/preview_v052.py`只用合成样本，禁止serial.Serial和socket.socket，枚举返回空。使用真实MainWindow，生成PNG和result.json；数据不是板卡输出。

| 窗口（逻辑像素） | 波形分区/终端 | 波形占比 | 工具行数 |
|---|---|---:|---:|
| 1920×1080 | 782/260 | 75.05% | 1 |
| 1280×800 | 572/190 | 75.07% | 2 |
| 1020×680 | 482/160 | 75.08% | 2 |

同三种尺寸在QT_SCALE_FACTOR=1.5离屏复测通过；主控件均包含在中央区域内、串口未连接、发送任务未开始。证据在`build-preview/runtime-v052/preview-1/`与`preview-1.5/`，已检查宽窗口与最小窗口图片。比例指上下分区，不把工具条/坐标文字面积算成网格。

验证覆盖：布局恢复、旧工程保持420:260、schema6往返、最小窗口控件边界、选中B卡暂停仅产生一次状态变化、打开周期菜单不启动发送、任务错误可见和右侧时间刻度不越界。现有分隔条鼠标/键盘/Home测试全部保留并通过。离屏检查不等于所有显示器/DPI下的人工交互验收。

## 构建与交付

两种构建退出0（PyInstaller6.22.3）。日志为runtime-v052/build-onedir.log、build-onefile.log。两种EXE均从F盘空工作目录启动并观察到标题Astrspire v0.5.2、Responding=True；目录版自然关闭，单文件版留给用户查看。启动检查仅证明主窗口正常，不等同在包内重跑728项或人工逐项交互验收。

| 产物 | 字节 | SHA-256 |
|---|---:|---|
| dist/Astrspire-v0.5.2.exe | 68852194 | 450E3D34616FC0E38F90DC61280F097117C8A387BEB94A57D89765FC91214E9C |
| dist/Astrspire-v0.5.2/Astrspire-v0.5.2.exe | 2678187 | 6624E2E940D869F401508ACE4A820B5EAF8E75E5BD71D5AD7AF1E1BF4B17A527 |

目录版需完整_internal。旧v0.5.1 EXE仍为DB456FC02876EEEA3C45EDA8E7DDD33587E89239D7807FB40E10D43BD1624054。源码白名单包不含原版EXE、captures/private、.venv及缓存；源码SHA清单、测试XML、截图、构建/启动记录分别保留。本地私有golden不分发，在缺该证据的环境相应可选测试可能跳过。

实际产物及目录占用见runtime-v052/artifacts.json、storage.json。没有下载依赖；可控新写入在本项目F盘，构建中间件、测试目录及日志留作证据。Windows自身不可配置写入未全面审计，不宣称C盘绝对零写入。不删除用户文件。

## 已知问题未修复

控件排在波形前的1点历史问题、实时固定轴丢失、分析批次滞后、暂停恢复追平、图像修改未标脏、原生非法叶值漏报、复古强度及海报化量化问题均保留。多卡性能、动态命令库、完整VOFA等效和实机验收都不在本版结果中。详见feature-status-0.5.2.md及上一轮规划。

onefile仍使用F盘工程目录解包，无F盘电脑未验证。旧软件不能读取更高schema的限制不变，本版没有升级schema。
