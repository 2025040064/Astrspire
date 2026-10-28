# VOFA+ 图像效果静态清单与自主移植记录

日期：2026-10-22。对象：本机安装的 VOFA+ 1.3.10 `E:\app\x64\plugins\widgets\image\effects`。方式：只读读取 QML 参数声明，不启动原程序，不执行 QML/shader，不复制其代码或资源。

## 证据与边界

- 以下 QML 仅用于确认效果名称、参数名、默认值和参数数量；散列固定本轮读取对象。
- 原效果由 Qt Quick shader 实现。本软件改用有界 CPU 算法，**不是 shader 等效、不是逐像素一致**，也没有使用原 shader 源文件。
- 每个效果在本软件中只暴露一项有界参数；原生两参数效果（Isolate 的 Hue+Width）只移植色相，窗口宽度固定为 30°。

| 文件 | SHA-256 | 参数（QML 观察） |
|---|---|---|
| `EffectBillboard.qml` | `918f55bc47fea9ca7f032c964f2a560ac9f40c1de2695320368c67f67cc6bc46` | Grid Spacing，默认 0.5，映射 0–10 |
| `EffectBlackAndWhite.qml` | `64f29f69f10c7571e192bb74a246f9e8017a5226ff7e9f0d8c197c5e9f77b581` | Threshold，默认 0.5 |
| `EffectGaussianBlur.qml` | `a5edc5563bde2d0e2ec53b652347bd7a3f75fe065c6988dd2b4011c4f3693f42` | Radius，默认 0.5 |
| `EffectGlow.qml` | `c8bcd8c49c42eb076d0629e20cd856b753eefe5d51e79bb318aac0b405e49200` | 无参数 |
| `EffectIsolate.qml` | `1a6635ac7ac973fce751258d9c133faf8080a530c7dc87ac91d859b0b1763d0b` | Hue 0.5、Width 0.5；映射 0–360°、0–60° |
| `EffectPixelate.qml` | `a89f291a1ec6c2655497bd23e2c2172daf40e94bb6f3437a6cf421f05ab50d4c` | Granularity，默认 0.5，映射 0–20 |
| `EffectPosterize.qml` | `08f5564f78f29dd922935d9796d691cb9afe963635ff446788dfd60627efaabc` | Gamma，默认 0.5；固定 8 色 |
| `EffectSharpen.qml` | `3af022f05da950b6bc2c6b3fd458bb0630ba011bbac2e3a2073ed9b451e3b7c3` | Sharpness，默认 0.5，映射 0–18 |
| `EffectShockwave.qml` | `9b26b3450103ee8221c9fbde96c33c8cf796fba1730616d9cedfecacd24285f1` | Amplitude，默认 0.5；含时间循环 |
| `EffectSobelEdgeDetection1.qml` | `224ccd541cf10824f8c66a647f363d7916e1f40671f57886486858c1085e0b38` | Threshold，默认 0.5 |
| `EffectToon.qml` | `0b92df0fd1e67eaf75b352cf6d26f001e9258d5c15be22c42f6a0247286bfb23` | Threshold，默认 0.5；固定 quantize 8.0 |
| `EffectVignette.qml` | `940924e023d2ce7aacd6d6963a51d61a471e0565f40bb12343f4a507592d4c7a` | 无参数 |
| `EffectWarhol.qml` | `413afa9363a3d3085a3f9d3650c6425fad877e5af1715728c49aa324a655dc5e` | 无参数 |
| `EffectWobble.qml` | `1721fdafdd27213096eeb1842fd6b6c11ae77d27376f94d4347fd3ea794eb725` | Amplitude，默认 0.5；含时间循环 |

`EffectEmboss.qml`、`EffectSepia.qml` 由既有实现覆盖；`EffectPassThrough` 对应“原图”。

## 本软件映射

`src/qx_stc_isp/vofa/image_effects.py` 新增：

| 效果 | 本软件参数 | 实现要点 |
|---|---|---|
| 海报化 `posterize` | 伽马 0–100，默认 50 | 伽马校正后量化为 8 级 |
| 卡通 `toon` | 阈值 0–100，默认 50 | 5 级量化 + 亮度梯度描边 |
| 泛光 `glow` | 强度 0–100，默认 60 | 高亮提取 + 3px 盒式模糊叠加 |
| 暗角 `vignette` | 强度 0–100，默认 60 | 按到中心距离平方衰减 |
| 扫描线 `billboard` | 网格间距 0–100，默认 50 | 2–12px 网格 + 隔行压暗 |
| 波普 `warhol` | 强度 0–100，默认 60 | 四象限色相旋转 + 饱和度提升 |
| 移轴模糊 `tilt_shift` | 强度 0–100，默认 60 | 中央带清晰、上下渐变模糊 |
| 色相隔离 `isolate` | 色相 0–255，默认 128 | ±30° 内保留并增强，外转灰度 |
| 冲击波 `shockwave` | 幅度 0–100，默认 50 | 静态相位径向位移 |
| 摆动 `wobble` | 幅度 0–100，默认 50 | 静态相位水平正弦位移 |

## 与原生行为的明确差异

- 原生使用 GPU shader 与动画时间循环；本软件是单帧 CPU 结果，冲击波/摆动不连续动画。
- 原生 Isolate 有两个参数；本软件固定窗口宽度，只保留色相选择。
- 原生参数为 0–1 浮点；本软件沿用工作区 schema4 的整数参数范围（0–255 或 0–100），由效果自行解释。
- 未复刻 `EffectTiltShift` 的完整语义（本轮未读取该文件）；当前“移轴模糊”是自有近似。
- 所有效果受 16,384 像素 / 最长边 256 的预览预算限制，与原生全分辨率 GPU 渲染不同。

## 验证

- 新增测试覆盖 10 个新效果的参数下限/上限、图像尺寸和输出差异，以及 `isolate` 越界参数拒绝。
- 完整套件：**617 passed in 23.07s**（2026-10-22）。
- 只验证了离屏 CPU 输出，没有启动原 VOFA+、没有比较原渲染截图、没有真实硬件图像流。
