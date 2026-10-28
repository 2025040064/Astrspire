# VOFA+ 1.3.10 静态功能清单与边界

日期：2026-10-22。范围：用户指定 `E:\app\x64\vofa+.exe` 与其安装目录中的插件描述/QML。本轮按用户最新要求只推进 VOFA，STC 协议和硬件注册保持原状；不运行 GUI、串口、网络或功能测试，不处理授权保护。

## 目标身份和证据层级

- 本轮只读读取的 EXE SHA-256：`D4E53EA60484293B1A701EBE7A4D2DB38FEE267EBEAB24746565F6B14EC0C370`，与历史分析对象一致；文件散列只固定分析对象，不证明真实性或功能正确。
- 历史 REA 记录：`ev_f3ac6eb793bdbe225fe98abcd855998acf24bfd3c7c3e1d9b496bf38e9f01454` 为过程/字符串范围；`ev_9f443c3d34426cef0868bac697929edc989bcb52eb94df597215491698058fcc` 涉及 `FUN_14000dcb0` 的 CSV/JPG 数据生成选择。它们是历史静态观察，不是本轮运行结果。
- 下面的 QML/JSON 结果是本轮直接读取既有文件所得的**静态观察**；没有导入执行这些 QML，没有复制其代码、图片、STL、SVG 或 shader 到自主实现。
- `plugins/widgets/RQILPJ.json` 与 `zPnXxb.json` 是开发工程路径/文件清单，不是标准插件功能 manifest。它们能辅助定位资源，不能证明每个功能在当前安装版本中可访问。
- 原程序在早期窗口观察中出现授权模态。此次未启动原程序，也没有绕过或重新探测授权，故完整 GUI 可达性仍未确认。

## 数据引擎和图像

| 项目 | 本轮观察来源 | 可确认内容 | 仍未知 / 自主实现约束 |
|---|---|---|---|
| FireWater | `plugins/dataengines/firewater.json`；本轮 DLL 静态证据 | 行结束解析；逗号数值通道；可含文本前缀；图像头和按长度载荷分支见后文 | 超长/坏行、异常重同步和所有边界仍未运行确认 |
| JustFloat | `plugins/dataengines/justfloat.json`；本轮 DLL 静态证据 | 小端 float32 通道，帧尾 `00 00 80 7F`；双标识和 28 字节图像头见后文 | 坏帧、通道上限和所有尾字节冲突处理未运行确认 |
| RawData | `plugins/dataengines/rawdata.json` | 描述明确是不做采样和图像解析的原始字节模式 | 不得把新增 JPEG/PNG 解码称为官方 RawData 行为 |
| 图像控件 | `plugins/widgets/image/image.qml:11`、`:76`、`:226`、`:273`；`FUN_1400324b0` | 多图索引 `img_index`、数据图像提供器、动态图像通道菜单、刷新；编码格式表见后文 | 各插件实际解码能力、异常图像和全格式运行结果未确认 |
| 图像效果 | `image.qml:241` 与 `image/effects` 文件 | 菜单按效果 QML 枚举，效果通过单独组件加载；存在灰度、浮雕、模糊、锐化、像素化、复古、边缘等入口 | 文件存在不证明全部效果可运行；不能导入原 QML/shader 冒充自主实现 |
| 图片保存的控件设置 | `image.qml:306` | 标题、图像通道、边框/名称、效果文件和参数会进入控件上下文 | 整个 VOFA 工程格式/版本、顶层存储方式未确认 |

标准连续 JPEG/PNG 帧流可以作为**本项目自定义数据模式**实现，但不能仅因能够解码 JPEG/PNG 就声明与 VOFA 图像封装兼容。下文已建立两个 native 引擎的静态图像封装证据，尚无运行回放结果；本地图片目录回放同样不自动等于原程序 Demo JPG 数据源。

## 控件可证明的配置与交互

| 组件 | 关键静态观察 | 对自主实现的建议 | 未确认项 |
|---|---|---|---|
| `bound_bt` | 两套数值/十六进制参数，命令绑定和控件上下文 | 提供按下/释放命令参数，加载不发送 | native 命令模型完整语义 |
| `ExtraButtonToggle` | `reverse_logic`、通道阈值；接收值只更新外观，使用等待标记避免触发发送 | 反馈更新与用户点击发送分离 | 无绑定/等待反馈时的完整时序 |
| `ExtraButtonDelay` | 阈值、默认 3000 ms 延迟、回弹选项；用户按压状态和两套命令参数 | 长按触发、释放/失焦取消；导入不计时 | 所有平台的原生触发时刻与计时容差 |
| `slider` | 范围、步进、目标值；参数/命令/通道/数值/名称/主题上下文 | 用户提交才发送，采样反馈不触发 TX | 发送频率与拖动中发送的全部原生规则 |
| `ExtraDial` / `MaterialDial` | 存在独立旋钮控件与配置上下文 | 使用自有 Qt 旋钮实现范围/步进/命令 | 两种旋钮全部视觉和输入差异 |
| `MaterialJoystick` | 范围/步进/回中；值变化和释放使用不同参数；加载与连接门禁 | 可提供 X/Y 与释放/回中命令，键盘替代拖动 | 原生十六进制模板/参数占位规则 |
| `light` | 下/上阈值、三色模式、绑定通道颜色、固定尺寸选项 | 只读阈值显示和可配颜色 | 各阈值边界的完整 native 行为 |
| `gauge` | 数值范围、小数、标签尺度、基准点、危险区和反向 | 有界绘图与真实数值显示分离 | 原生危险区组合与布局细节 |
| `circular_gauge` | 起止角、刻度数、范围、危险比例、小数、字体/标签设置 | 支持范围/刻度/危险区的自主圆形仪表 | 全部原生刻度标签计算 |
| `cube` | Euler/四元数模式；标量+三轴通道；角/弧度；位置/角度偏置；自动居中；物体/世界坐标轴；尺度 | 三轴 wireframe 可作为子集；完整四元数与偏置另列 | native 角次序、坐标系、模型导入、GPU渲染和全部姿态约定 |

`MaterialJoystick.qml` 的本轮 SHA-256：`D821D3034E63D0D85045698689B94B5FE987FEA2C9DD461E8407FE4B6FC0928D`；`image.qml`：`56FD9956DA5B397A77BBBF93A342CD856BC192B2A12E21CBC03B19B67CED3BB0`。这些散列用于本机证据定位，不作为本项目分发资源。

## 控件上下文与主题

已观察：多个控件提供 `get_widget_ctx` / `set_widget_ctx` 形式的上下文；滑块保存范围/步进/目标值以及各绑定菜单上下文；摇杆保存范围/步进/回中和参数/命令/名称/主题上下文。加载前后有 `loading` 状态，因此加载控制状态不应被当成用户发送动作。

已观察：安装目录主题 JSON 包含颜色集合、通道配色和背景属性，背景可能引用同目录图像。自主主题可提供颜色/背景参数，但不复制原图像、字体或专有主题代码。

未知：原生工作区顶层结构、文件扩展名/版本兼容规则、插件安装包格式、第三方原生 DLL ABI 与配置迁移方式。当前本项目自有 JSON / 数据式插件不得宣称可直接运行 VOFA Qt5 DLL 或原 QML 插件。

### 已安装控件配置字段清单

以下只整理配置键/含义，不复制 QML 实现。这些控件在安装文件中的 `get_widget_ctx` 都返回包含 `path` 与 `ctx` 的对象，`ctx` 再分根节点 `.` 和命名子组件；这证明**单个控件局部结构**，不能推出顶层工程 JSON、通用 `get_ctx` 的全部内容或迁移规则。下表不是“已在自有软件完整实现”的清单。

| 控件与本机位置 | 根级/专用配置 | 嵌套绑定及显示配置 |
|---|---|---|
| `bound_bt/bound_bt.qml:344` | 通用控件 ctx | argument_menu、cmd_menu、name_menu、theme |
| `ExtraButtonToggle/ExtraButtonToggle.qml:605` | reverse_logic、threshold | argument_menu、ch_menu、cmd_menu、name_menu、theme |
| `ExtraButtonDelay/ExtraButtonDelay.qml:611` | reverse_logic、threshold、delay、bound | argument_menu、ch_menu、cmd_menu、name_menu、theme |
| `slider/slider.qml:503` | from、to、step_size；夹在范围内的 target_value | argument_menu、cmd_menu、ch_menu、value_menu、name_menu、theme |
| `ExtraDial/ExtraDial.qml:643` | from、to、step_size；夹在范围内的 target_value | argument_menu、cmd_menu、ch_menu、value_menu、name_menu、theme |
| `MaterialDial/MaterialDial.qml:546` | from、to、step_size；夹在范围内的 target_value | argument_menu、cmd_menu、ch_menu、value_menu、name_menu、theme |
| `MaterialJoystick/MaterialJoystick.qml:453` | from、to、step_size、boundEnabled | argument_menu、cmd_menu、name_menu、theme |
| `light/light.qml:472` | top_value、bottom_value、three_color_mode、light_color_link_ch、light_color、reverse_logic、threshold_model_ctx | argument_menu、cmd_menu、ch_menu、value_menu、name_menu |
| `gauge/gauge.qml:856` | from、to、decimal、base_ratio、label_scale、danger_from_ratio、danger_to_ratio、danger_on、danger_reverse | ch_menu、name_menu、value_menu、theme |
| `circular_gauge/circular_gauge.qml:878` | from_angle、to_angle、tick_count、from、to、danger_ratio、danger_on、danger_reverse、decimal、font_scale、label_inset | ch_menu、name_menu、value_menu、theme |
| `cube/cube.qml:1174` | scale、quaternion_mode、angle_or_radian、is_auto_center、is_show_obj_axis、is_show_world_axis | scalar/x/y/z 通道、位置偏移开关、角度/位置偏移、center_point、root_transform 旋转、obj_length、obj_world_length、theme、模型 selected_file |
| `image/image.qml:305` | title、img_index、is_hide_name、is_hide_border、is_show_effect_setting、parameters、effect_file | 通用控件 ctx；效果参数由效果组件恢复 |

滑块、两种旋钮、摇杆加载配置时明确设置 loading，避免恢复配置触发用户动作；图像效果有 first_load 标记。其他控件虽有通用恢复入口，但不得据此扩大声称所有原生加载路径完全无发送副作用。

## 本轮 REA 路径与阻碍

当前 MCP 会话没有已打开目标或保留的 VOFA Evidence。为遵守 F 盘存储规则，没有直接使用未设 F 临时目录的 MCP native 打开路径。

改用已安装的 REA 6.5.0 CLI，进程级 TEMP/TMP/TMPDIR 指向 `F:\A_LIU_Astrspire\VS\51烧录\build-preview\runtime-v040\rea-static`，JDK/Ghidra 只读复用 C 盘既有安装；目标原文件保持只读。第一次聚焦 `FUN_14000dcb0` 的请求发生 `execution_failure`：Ghidra 自动分析结束，但 Java bridge 启动因 `BSim.jar` 的 `AccessDeniedException` 失败，没有产生可引用的新反编译结果。仅此失败不能说明目标存在加壳、保护或反调试。

随后仅对该静态分析进程申请必要权限、收紧库搜索 PATH 后重试，取得成功：

- 新 Evidence：`ev_e284d43be21b6399726432ae8b6b3e5128fd77cf49041e47da40612d0c4e134f`，操作 `procedure_pseudo_code`，过程 `FUN_14000dcb0`。
- 新快照：`captures/private/vofa-20261022-hour.snapshot.json`；保存完整 REA 记录、目标身份和 profile，而非人工复造反编译文本。
- 本轮新观察：创建独立 CSV/采样与 JPG 图像定时器；按 FireWater / 非 FireWater 路径分别连接采样与图像读取槽；CSV 目录筛选 `*.csv`，图像目录筛选 `*.jpg`，两者使用 `QCollator` 的 numeric mode 排序。
- 可见槽名：`readyReadFireWaterSlot`、`readyReadJustFloatSlot`、`readyReadFireWaterPicSlot`、`readyReadJustFloatPicSlot`。槽名和连接分支确实存在，但尚不能由连接函数确认槽内部的逐字节协议。
- 反编译输出是 Ghidra 观察所得伪代码，不是原始源码；PDB 没有载入保证，过程/类型名和边界有工具局限。未运行原程序或校验其功能。

新增查询记录（完整记录保存于上述快照）：

| Evidence ID | 观察 | 边界 |
|---|---|---|
| `ev_9371ca201c31faeee694c18b99e66d1d5320922c02ab185049c595cf9117e1e8` | 已定义字符串包含 `VOFA+.Plugin.DataEngineInterface`、`QPluginLoader` 导入、控件上下文名字及图像槽名字 | 只能证明标识/导入/字符串存在，不能证明 Qt5 插件 ABI 的完整函数签名或兼容性 |
| `ev_e498b7839e7d0c89029b7f677e81d38d3fc4f5fb58c4ef427926d6cefea4bb8f` | 对 `readyReadFireWaterPicSlot` 的字符串追踪得到定时器绑定调用点 `0x14000e0f5` / `0x14000e0fc`，位于 `FUN_14000dcb0` | 不把绑定处当作槽实现；该查询没有建立图像封装字段 |

后续为减少重复导入，在 F 盘运行仅分析用 MCP 客户端，复用同一 Ghidra 数据库；辅助脚本位于 `build-preview/runtime-v040/rea-static`，不随本项目 EXE 分发，不更改全局 REA/Codex 配置。

## 新确认：Demo 图像帧生成（不是实机抓包）

通过 RTTI 标注的 `DebugDemoIOSlotsObj::vftable`（`0x140060128`）取得元方法入口 `FUN_140042870`；`QMetaObject` 指向的字符串/方法表共同建立了 slot 次序，而不是依赖过程地址邻近推测：

| 元方法号 | 名称 | 目标过程 |
|---|---|---|
| 0 | onTickTimeoutSlot | `FUN_14000e4c0` |
| 1 | readyReadJustFloatSlot | `FUN_14000edb0` |
| 2 | readyReadJustFloatPicSlot | `FUN_14000eb50` |
| 3 | readyReadFireWaterSlot | `FUN_14000e7a0` |
| 4 | readyReadFireWaterPicSlot | `FUN_14000e4f0` |

元方法证据：`ev_18c1a0203599f32753f4e1d014938fb19fc33ea25aff5d900b133553d8355a80`（分派）、`ev_c7d36c18915be1f6f3040b73944ace094febe8cf2902b2e5e5d01534b78eba90`（QMetaObject 字段）、`ev_417a96b92920f2fe18eb77df1f7d612ebadc1654feb01699043405dd57922ab9`（方法表；字符串表随同目标快照保留）。

### FireWater 图像生成头

静态观察：`FUN_14000e4f0` 打开已选 JPG、读取完整字节，先传送 ASCII 头 `image:0,<N>,30,40,27\n`，再传送恰好 N 个文件字节。N 来自读取后的 QByteArray 长度。没有在此生成路径观察到图像载荷之后的额外尾字段。

- 伪代码：`ev_c281cf5751cff42d742edabbada04f7fbd60540670c1275303a36fc65bf6b231`。
- 汇编：`ev_6911e013682ad931d9248d04fa525beae58d4c2b097613cdf938b9cbbc5c5410`。
- 可复核 callsite：`0x14000e648` 写入栈参数 27；`0x14000e650` 写入栈参数40；`0x14000e658` 置 R9D=30；`0x14000e65e` 置 R8D=N；`0x14000e66d` 调用格式化函数。`0x14000e6b7` 送头，`0x14000e735` 送 N 字节文件数据。

接收端及 ImageProvider 的后续静态追踪已经建立宽/高/格式语义，见后文。不能仅凭这里的 30、40、27 常量推导所有原始像素布局。

### JustFloat 图像生成头

`FUN_14000eb50` 先传 28 字节头，再传恰好 N 个原 JPG 文件字节。x86-64 目标汇编明确按连续 DWORD 写入，布局如下：

| 偏移 | 长度 | 字节序 | 本路径生成值 |
|---|---:|---|---|
| +0 | 4 | 小端 | 0 |
| +4 | 4 | 小端 | N（文件载荷字节数） |
| +8 | 4 | 小端 | 200 |
| +12 | 4 | 小端 | 300 |
| +16 | 4 | 小端 | 27 |
| +20 | 4 | 小端 | `0x7F800000` |
| +24 | 4 | 小端 | `0x7F800000` |
| +28 | N | 原样 | JPG 文件字节 |

这不是7个 float 值：前5项按 DWORD/整数语义写入，末2项是两个连续的 `00 00 80 7F` 标识；载荷位于这两个标识之后，而不是在两个标识前。

- 伪代码：`ev_fed7a527652ec829634301efb2e423182737d7ab2c313b1978751787bc44e802`。
- 汇编：`ev_c5ba10cd5c3f8706f567468e70f2a6bef5b965d081b399b28449c6d56aac45da`。
- Callsite `0x14000ecac` / `0x14000ecb3` 写双标识；`0x14000ecc4` 写 N；`0x14000ecc7` / `0x14000ecce` / `0x14000ecd5` 写200/300/27；`0x14000eced` 设置头长28；`0x14000ecfb` 送头；`0x14000ed3a` 送 N 字节文件。

### 输入解析的已知链与缺口

生成端经 `FUN_140031b20` 进入本机引擎输入缓冲，后者调用 `FUN_140030fe0`。`FUN_140030fe0` 通过当前数据引擎对象虚表首函数处理缓冲区，再处理返回的采样/图像集合，因此完整识别规则依赖选中的数据引擎 DLL，不能只研究主程序格式化函数。

新证据：`ev_7d9922a642e5d8b7232833f10f6db8eeb32eb3c965217682fe6c438ccd535d97` 与 `ev_1c962c9455a3e97453b226f75e26ca7ff5812b57fbd73d215f7c20a9dca2c946`。本节是静态观察，不是串口、网络或板卡输入回放测试。

## 新确认：引擎接收与图像格式

这一节为 **observed / shipped-artifact 静态观察**，不是目标程序运行、串口回放或硬件测试。

### FireWater 引擎

目标 `E:/app/x64/plugins/dataengines/firewater.dll` 的 SHA-256 为 `16D1CB33B037DBA81FF5B9034CF8D34866C3C48056BDEB2687CD5D5D521E596E`。接口虚表 `0x1800055f8` 的首项为 `FUN_180001370`。

- `FUN_180001370` 按 LF 识别行，按冒号拆分后检查倒数第二部分去空白后为 `image`，最后一部分必须为 5 个逗号字段。
- 5 项按十进制整数解释为图像索引、载荷字节数 N、宽、高、格式；接收数据不足 N 字节时等待更多数据。
- 载荷取 LF 后紧随的 N 个字节，不依靠 JPG EOI 扫描；跳过这 N 字节再处理后续输入。没有观察到载荷尾标识。
- `FUN_180002ad0` 保存图像对象：`+0` 为 QByteArray；`+8` 格式；`+0xC` 字节数；`+0x10` 宽；`+0x14` 高。
- 证据：`ev_159dcd3056f29adfc71e9f7f9b0d00844793ba200104955bbf8e833288e058b5`、`ev_2d601bc88b4cf0bda6069f97c4ba53f2887405d3db9bb82a207d49b6dc095921`。

### JustFloat 引擎

目标 `E:/app/x64/plugins/dataengines/justfloat.dll` 的 SHA-256 为 `67FA403C397136151B20501C5351A4E4FA318B290E0D611DF9DF47FC0612B9B3`。接口虚表 `0x180004530` 的首项为 `FUN_1800012b0`。

- 识别 `00 00 80 7F`；单标识走采样分支，连续双标识走图像分支。
- 图像分支要求从当前候选头开始到双标识结束共 28 字节；前 5 项按小端整数读取，依次为索引、N、宽、高、格式。
- 紧随头之后读取 N 字节，不足时保留等待；按长度跳过图像数据。没有观察到图像载荷后额外尾字段。
- 证据：`ev_f33406ed36990c83412f7ed4873178150ce825268b7591446fb032dc7de6dcfa`。

两种引擎中原生代码的负索引、超大长度或异常输入处理不能被当成建议行为；自主实现应保持有界内存、拒绝危险长度，明确记录与原生不安全边界的差异。

### ImageProvider 解释格式字段

`ImageProvider::vftable` 位于 `0x140065738`，自定义 `requestPixmap` 为 `FUN_140034c80`；该入口将十进制图像索引交给 `FUN_1400324b0`，随后由 QImage 转 QPixmap。`FUN_1400324b0` 检查引擎、索引和正载荷长度，然后执行如下分支：

| 格式字段 | 交给 QImage 的编码格式名 | 观察级别 |
|---:|---|---|
| 25 / `0x19` | BMP | observed |
| 26 / `0x1A` | GIF | observed |
| 27 / `0x1B` | JPG | observed |
| 28 / `0x1C` | PNG | observed |
| 29 | PBM | observed |
| 30 | PGM | observed |
| 31 | PPM | observed |
| 32 | XBM | observed |
| 33 | XPM | observed |
| 34 | SVG | observed |

这些分支调用 `QImage::fromData(payload, N, formatName)`，宽和高字段未用于编码图像解码。其他数值走 `QImage(rawData,width,height,format,null,null)`，格式直接作为该程序使用的 **Qt5 QImage::Format**，没有显式 bytesPerLine 参数。未逐项确认原始格式值、每行对齐、端序或插件可用性，不据此宣称支持所有原始像素布局。自主程序采用 Qt6，不能盲目把未确认数值当 Qt6 枚举。

可复核调用点：`0x1400325e0` 取 N；`0x1400325f3` 取载荷指针；`0x1400325fb` / `0x1400325fe` 置格式名与 N；`0x140032605` 调用 fromData。原始分支 `0x14003262f`、`:633`、`:637` 分别取格式、高、宽，`:660` 调用 QImage 构造。

证据：`ev_664ea8a999d6fba5b9a9e8264287341c5f1389d08c40c57eba1c7d883f062a36`（requestPixmap）、`ev_cd686bbd28b65d8ebf1639ea61711c62c14abdee7792edccd56510c99aac4109`（格式分支）、`ev_64d18695b0dca67e08d1ffe35296baee601e156a927d43b707c9dd473ae96227`（汇编参数）。解码失败回退到原有占位图；占位资源未复制。

## 新确认：Demo CSV 的实际转换

- FireWater 的 `FUN_14000e7a0` 每次读取选中文件的全部字节并原样交给引擎，不在这里生成额外表头或时间列。证据 `ev_aaffdd0fa15f424c2ee3c81d32de45ae1b81e1b5a9460c743d44f058f9d20ad4`。
- JustFloat 的 `FUN_14000edb0` 逐行读取并去空白，以逗号拆分，每个字段转换 float32，按行附加一个小端 `0x7F800000`，整个文件转换完后统一发送。汇编 `0x14000f13a` 明确设置 `R8B=0x2C`，`0x14000f145` 为 split 调用。证据 `ev_d1214158f7329197b269a00928c3409f1ebb84e88273b16e6bd1f2f5a48c383a`、`ev_79f613b8b79b5b503701c1198bbde304d8e8cee2530fdd4c38301be3b734e506`。
- 这条路径没有观察到带引号的 CSV、特殊表头或保留时间列分支；不能把这一点扩大为所有导入功能都不支持表头。自主 CSV 导入的严格数字校验属于刻意的安全差异，不复制无效 toFloat 的默认为零行为。
- CSV 和 JPG 文件名均在 `FUN_14000dcb0` 通过 QCollator numeric mode 排序；定时触发单位是选中的整个文件，不是每一行。

## 原生工作区与插件的收口证据

### 原生顶层工作区：unknown

主程序包含 `vofa+.tabviews.json`、`vofa+.cmds.json`、`vofa+.config.json`、`save_ctx`、`load_ctx`、`context_dir_path` 等常量。本轮逐项 xref 返回空，部分常量处于 Qt 编译资源表；空 xref 仅表示当前分析器未建立直接引用，不能推出逻辑不存在。

`FileIO::vftable` 位于 `0x140062808`，元调用 `FUN_140048010` 暴露 setSource、setHomeSource、read/read_raw、write/write_raw、append、copyTo/cutTo、exist、remove/rename、mkdir、filesInDirectory、dirsInDirectory、applicationDirPath、removeDir/copyDirTo。静态入口 `FUN_14001cef0` 是通用 QFile/QTextStream 文本写入，不包含 top-level 工作区键解释。因此这些文件名和 FileIO 不能用于臆造完整 schema。

证据：`ev_b9b168eff42399939c73b7e24fd28241d2260efeeb595276d61afa4af6a047ec`（FileIO 分派）、`ev_28e90f26ee8ae6b24e79c3cf97ecf7ea78fd9da210797cfd576efeab43278eaf`（FileIO 元字符串）、`ev_a6fc3194e5f8b5953a04e104edad40330a9b1394b8c8855d350a9381d58eb6b8`（通用文本写入）。未调用这些函数，未创建/删除原程序文件。

结论：自有 schema 4 工作区可保存自主功能，但没有足够证据声明可无损载入/导出 VOFA 原生顶层工程。本轮不为了填满覆盖矩阵而把猜测写成格式。

### 数据引擎原生 ABI：仅建立加载和输入边界，完整 ABI 仍 unknown

- `FUN_140026060` 组成根路径下 `/plugins/dataengines`；`FUN_1400262f0` 用 QPluginLoader 的 setFileName/load/instance 取得对象。
- `FUN_1400263a0` 枚举目录候选，读取实例 metaObject 的 className，并保存文件名和类名。
- `FUN_1400265d0` 切换时卸载旧实例，检查选中实例类名匹配，再通过 IID `VOFA+.Plugin.DataEngineInterface` 作 Qt 元转换。
- `FUN_140030fe0` 的当前引擎输入边界是接口虚表首函数，参数为实例、原始字节指针、长度；随后读取接口内采样/图像集合。集合中有 Qt5容器，不是稳定 C ABI。
- 证据：`ev_dee17332db958c4d2874140d397c7129ca8fbbca6d2533a56cc3c022ddd632a4`、`ev_cc4b0f79b1f0b640ff02a96511792017e016bb91b81c3f9490076d95b6a5161e`、`ev_d135186946b4d494b58e087b7c266e93b1420887c3b6a53466c2b3141c7a9703`、`ev_38b0e17e028715ba97191aea122740cdcba9f1b1bc0c6b3f15d488f30fe1d584`。

没有加载运行任何第三方插件；未建立整个对象生命周期、容器布局、版本兼容、异常边界和销毁约定。PySide6 自主程序的数据式插件不能直接称为原生 Qt5 DLL 插件兼容，不新增未知 DLL 执行入口。

## 元对象公开的波形/日志能力（声明级静态观察）

`RingBufferWave` 元对象通过 vftable → metaObject → 字符串表建立关联，表地址 `0x140312A10`。Evidence `ev_f09c25a5488cfcbc15616880ecbf53f90dc7a42808fe769a6bfc961244a3feac` 保留完整字节。下面是已声明的接口，而不是算法或完整 UI 可用性的证明：

| 能力分组 | 已观察的接口名 | 未确认范围 |
|---|---|---|
| 波形/柱形/直方/频谱开关 | if_draw_wave、if_draw_bar、if_draw_histog、if_draw_freq | 四类绘制、谱线归一化与边界的完整计算 |
| 直方与频谱参数 | histog_gap_num、histog_range_min/max、freq_gap_num、freq_num_effective_points、freq_list、freq_min/max_value | 分桶、窗函数、FFT 点数/补零/频率标定；不能据名字任选算法并叫原生一致 |
| 二维/X轴 | two_d_mode、axis_x_index、set_line_xindex、unit_a/unit_b/unit_text、axis_x_begin/end | 轴取样、插值、单位映射完整行为 |
| 显示与自动范围 | line_width、auto_mode、autoCenter、autoFill、autoBar、scale、offset、visible | 原生自动填充/居中计算和触发规则 |
| 性能/缓存显示 | optimization_on/max_count、fake_continuous、num_points、visible_count、draw_index_begin/end | 压缩/抽样算法；不能把自有 LTTB 或步进采样自动当成原生优化 |
| 通道管理 | add_line/remove_line、add_all_line/remove_all_line、line_index_list | 绑定/删除与工作区恢复的完整行为 |

`DataHub` 表地址 `0x140316A80`，证据 `ev_66ee98558c63c5b02f14f592a76bc2b18277f484b84a00af98384e30ebf008c0`：声明通道 scale/offset/xoffset/decimal/color/name、实时值 rt_values、max_size/effective_size、saveTo/loadFrom、image_count/image_tick、fps/real_fps、timeout_ms。文件格式和时间语义未由这些名字确认。

`TextBuffer` 表地址 `0x14030EC90`，证据 `ev_5534e2f15fea543db550d45f6902095b19908da2b0b9850cbfc9f140b73bd323`：声明编码器、HEX/echo/TX/时间显示、各方向 ANSI 格式、最大缓存、全部文本、清空和文本终端格式化。日志颜色/ANSI字段名字不等于已确认所有终端控制序列。

## 本轮边界和保存

本轮只读静态分析未遇到需要绕过的软件保护路径；原程序未启动，既有授权模态的可达性限制仍有效。没有执行设备、网络、GUI或自动化测试。所有新可控缓存/快照/导出均在 F 盘，原程序与安装插件未修改。

原生主程序快照为 `captures/private/vofa-20261022-hour.snapshot.json`；FireWater/JustFloat 分目标快照分别为 `vofa-firewater-20261022-hour.snapshot.json`、`vofa-justfloat-20261022-hour.snapshot.json`。过程/资源字节保存属于本机私有分析证据，不随自主软件分发。后续功能验证、原生工程兼容、完整插件 ABI 和授权后全部菜单可达性仍是独立未完成项。

2026-10-22 06:07:55 UTC 已通过 REA `close_binary` 正常保存并关闭自有 native 目标会话，主快照保存 79 条 Evidence、6,219,387 字节。分析客户端随后完成终止；未留下本任务拥有的打开 native 目标。中途导出的 image/public-meta/chart-meta bundle 也仅保留在 `captures/private`。

## 完整性结论

当前只能冻结上述**静态可证明的能力入口/配置清单**，不能冻结“全部可用功能已等效”的验收结论。协议和顶层工程格式未知处继续保留 unknown；功能自主实现可以推进，但必须把已编写、未运行、未兼容原生格式分开记录。
