# dev2 依赖与参考项目

运行环境：Windows x64、Python3.12.13、PySide6/Qt6.12.0、pySerial3.5、intelhex2.3.0。新增依赖严格固定为PyQtGraph0.14.0、NumPy2.5.4，完整解析结果见`uv.lock`；本轮已检查cp312-win_amd64/通用wheel并安装，无静默替换版本。

运行依赖写在`pyproject.toml`，测试/构建在`dev`组。`capture`组的Frida仅用于另行授权的分析，不是普通运行要求。依赖清单及安装记录保存在`build-preview/runtime-v060-dev2/`。

- [PyQtGraph](https://github.com/pyqtgraph/pyqtgraph)：实际绘图依赖，MIT；未修改其上游代码。
- [NumPy](https://github.com/numpy/numpy)：实际数组/计算依赖，主许可BSD-3-Clause，Windows wheel还带有其依赖的独立许可。
- [stcgal](https://github.com/grigorig/stcgal)：仅下载流程/异常处理交叉参考，未启用额外芯片、保护或供电选项。
- [COMTool](https://github.com/Neutree/COMTool)：仅参考串口操作流程，未整套移植PyQt5界面。
- [SerialPlot](https://github.com/hyOzd/serialplot)：仅参考基础波形功能，不复制其GPL实现。

构建脚本通过`--copy-metadata pyqtgraph --copy-metadata numpy`将这两个发行包的原始许可证、版权与元数据随包保留。目录版位于`_internal/*dist-info/licenses`；单文件在运行时解包为相同结构。不以参考项目的开源状态推断第三方专有程序可复制；本轮没有嵌入AiCube/VOFA+程序、资源或私有抓包。
