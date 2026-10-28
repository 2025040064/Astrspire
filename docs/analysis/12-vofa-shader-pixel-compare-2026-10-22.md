# VOFA+ 效果 shader 像素级对照（2026-10-22）

## 方法

- 只读读取安装包 `E:\app\x64\plugins\widgets\image\shaders\*.fsh`。
- 用 PySide6 + 桌面 OpenGL（NVIDIA RTX 4060，OpenGL 4.6 兼容上下文）在离屏 FBO 中编译原 shader，使用 128×96 测试图渲染参考图；这是 **shader 级参考**，不是原版 GUI 截图回放。
- 本项目效果用 `apply_image_effect` 在同一 128×96 输入上渲染。
- 指标：RGB 平均绝对差（MAD，0–255）、最大通道差 ≤8 的像素比例；差异图放大 4 倍。
- 原始 shader 参数取 QML 默认：threshold 0.5、gamma 0.5、numColors 8、granularity 10、targetHue 180、windowWidth 30、blur radius 0.5、sharpen 9、sobel mixLevel 0.5、toon threshold 0.5、wobble amplitude 0.025、time 0、shockwave weight 0.5/time 0。

## 结果（2026-10-22）

| 效果 | MAD | ≤8 像素占比 | 结论 |
|---|---:|---:|---|
| threshold | 0.00 | 100.0% | 等价 |
| shockwave | 1.31 | 99.0% | 静态相位下接近 |
| tilt_shift | 2.04 | 91.1% | 接近 |
| sharpen | 2.21 | 93.8% | 接近 |
| isolate | 6.07 | 59.7% | 近似 |
| pixelate | 7.78 | 73.0% | 近似 |
| wobble | 9.55 | 82.0% | 近似 |
| blur | 10.79 | 59.3% | 近似，核不同 |
| sepia | 14.29 | 0.0% | 色偏公式不同 |
| vignette | 15.54 | 31.0% | 暗角曲线不同 |
| toon | 17.40 | 18.2% | 量化/描边不同 |
| billboard | 18.87 | 35.4% | 网格算法不同 |
| emboss | 53.65 | 1.7% | 卷积核不同 |
| posterize | 64.88 | 6.3% | gamma/量化公式不同 |
| warhol | 119.59 | 0.0% | 原 shader 是三阶亮度配色，不是四象限色相 |
| glow | 160.47 | 0.0% | 原 shader 是 Sobel 式边缘辉光，不是 bloom |
| edges | 244.38 | 0.2% | 原 shader 输出白底黑边，自有实现是黑底彩色边 |

- 参考图与差异总览：`build-preview/vofa-vs-astrspire-pixel-20261022.png`。
- 源图：`build-preview/pixel-compare-source.png`；每种效果另有 `pixel-ref-*.png` / `pixel-ours-*.png`。
- 脚本：`build-preview/vofa_pixel_compare.py`。

## 边界

- 原版处于 `未授权`，主窗口不接受合成输入；没有在已授权 UI 中回放，因此上述“原版”是原 shader 的离屏渲染，不是原窗口截图。
- 没有把原 shader 代码复制进本项目；对照脚本只在本机 `build-preview` 中读取并编译安装包 shader。
- 结论：threshold 可视为等价；shockwave/tilt_shift/sharpen 接近；其余仍是自有近似，不能宣称完整移植。
