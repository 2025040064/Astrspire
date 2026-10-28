# v0.6.0-dev1 阶段验证 · 2026-10-23

## 范围与结果

本轮在现有Python3.12.13、PySide6/Qt6.12.0环境实施数据正确性和共享显示阶段，没有下载或安装依赖，没有打开真实串口、发送数据或刷写板卡。命令绑定等后续范围仍见TASK_V060.md，不能把本阶段称为完整v0.6.0。

新增22项离线回归。八类缺陷及增量接口均保留先失败后通过的证据：`build-preview/runtime-v060/red-*.xml`。完整回归命令：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='F:\A_LIU_Astrspire\VS\51烧录\build-preview\runtime-v060'
$env:TMP=$env:TEMP
.venv\Scripts\python.exe -m pytest -q --basetemp=build-preview/runtime-v060/pytest -o cache_dir=build-preview/runtime-v060/cache --junitxml=build-preview/runtime-v060/full-blocks.xml
```

结果：750 passed / 33.66s，0失败。此前正确性阶段741项、初版共享投影749项报告分别保留，不改写历史。Qt离屏环境曾提示字体目录缺失，不影响断言；不将离屏绘制称为桌面完整验收。一次新测试插入位置错误导致NameError，已修正，最终报告中无该错误。

## 性能

显式离线工具`tools/measure_shared_projection.py`运行20次，每次新增250帧；保留100000帧、3张波形卡。报告：`build-preview/runtime-v060/projection-performance-blocks.json`。

| 通道 | 初次可用/ms | 稳态p50/ms | 稳态最大/ms | 三卡离屏绘制/ms |
|---|---:|---:|---:|---:|
| 1 | 64.51 | 4.02 | 4.48 | 6.43 |
| 16 | 637.88 | 68.02 | 73.21 | 36.30 |

计时截至三个控件采用可用快照，不是只计排队。绘制单独计量，不能相加解释为持续帧率。单通道稳态满足50ms目标；16通道不作相同达标声明。初次建图及缩放细查仍可能较慢。

共享列为不可变元组；当前代次/配置不匹配的迟到结果拒绝应用。每个工作线程只有一项最新待处理请求。摘要缓存最多16×197块，每块最多64顶点；密集缺口回退原始绘制，避免缓存增长为第二份完整逐点历史。缓存预算不等于进程RSS上限，原始数据、活动快照和暂停快照另占工作内存。

## 交付边界

本阶段源码版本0.6.0.dev1，构建产物名Astrspire-v0.6.0-dev1；schema仍为6。旧v0.5.2单文件哈希复核未变：450E3D34616FC0E38F90DC61280F097117C8A387BEB94A57D89765FC91214E9C。既有STC核心与v0.5.2清单一致。

测试/性能证据、缓存及临时文件位于本项目F盘`build-preview/runtime-v060`；60秒回环复用旧工具，独立证据位于`build-preview/runtime-v050/v060-dev1-60s`。构建复用F盘`build-clean`，输出`dist`。下载量0；可控C盘项目写入未发生，Windows不可配置写入未逐项审计。

## 回环和启动补充

命令：`.venv\Scripts\python.exe tools\v050_loopback_acceptance.py --seconds 60 --fps 2500 --name v060-dev1-60s`。仅127.0.0.1 TCP，物理串口构造被禁止。150000帧/2400000字节，12项检查全部通过：序号完整、无重复乱序、历史有界和淘汰计数正确、无应用TX、暂停继续采集、换算保留100000点；错误数组为空。该检查不代表三板或10分钟验收。

版本/帮助文档变更后相关30项测试通过/2.73s。目录版与单文件均从F盘空工作目录启动，主窗口标题正确、Responding=True。WMI子进程查询遭系统拒绝；单文件改用进程名称、精确可执行路径及窗口标题确认（证据onefile-startup-fallback.json），并非把初次未找到窗口当成启动成功。只关闭本轮验证窗口，保留旧版程序和文件。最终文档更新后重新打包，哈希在artifact-sha256.csv记录。
