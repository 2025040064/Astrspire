# 下一项功能：图像卡片绑定实时索引

状态：**已完成（2026-10-22）**。实现与回归见本文末尾“完成记录”。

## 完成记录

- 新增 `src/qx_stc_isp/vofa/image_store.py`：按索引保存 payload/metadata/generation，32 MiB 载荷预算、64 MiB 解码/共享 QPixmap 预算；同一 generation 只解码一次，失败的 generation 也缓存错误。
- `controls.py`：image 卡片新增 `image_source=local|live`、`image_index=0..15`；实时卡片只读取共享 store，隐藏时不解码；无帧/坏图/淘汰显示占位；编辑/删除/载入后重新计算订阅，不发送数据。
- `control_editor.py`：表单增加来源与索引，缺字段仍按本地模式。
- `images.py`：`ImageViewWidget.set_decoded_frame()` 复用共享 store 的解码结果，独立图像页不再重复解码。
- `gui.py`：图像缓存改为 `ImageFrameStore`；独立页与卡片共享；约 10 Hz 合并刷新；窗口清理会话时清空 store；卡片实时图默认不套用独立页滤镜/旋转。
- 新增 `tests/test_vofa_live_image.py`：store 预算/固定/代次/错误缓存、spec 校验、隐藏卡片不解码、可见卡片显示、主窗口上传帧到卡片的集成路径。

回归结果：`617 passed in 23.07s`（`build-preview/test-results-v040-filter-params-2026-10-22.xml`）。仍只证明“实时图像卡片已接入且检查达到测试层级”，不声称完整 VOFA 等效。

## 要解决的问题

当前`ImageViewWidget`在独立页显示实时0–15索引图像；`ControlWorkspace`中的`image`卡片仅显式载入本地图片。需要让任意卡片选择实时图像索引，并继续保留本地文件模式，不把图片路径导入当作读取文件的授权。

## 已有接口

- `TelemetryEvent.image / image_index / image_metadata`已携带载荷和`(width,height,type)`。
- `FireWaterImageDecoder` / `JustFloatImageDecoder`已重组静态观察封装；不改变它们的协议字段或再推测新命令。
- `decode_native_frame(payload, metadata)`提供有界解码和拥有内存的QImage；raw格式仍实验，不通过卡片接入扩大兼容结论。
- GUI `_image_cache`保留按索引的`(payload,metadata)`，LRU总32MiB；`_image_render_timer`与`_show_cached_image`合并刷新独立预览。
- `_ControlCard._image_pixmap` / `_update_image()`可保宽高显示本地图片，`_choose_image()`是显式本地动作。
- `ControlSpec.settings`、`ControlEditorDialog`、schema4工作区和schema2独立控件已保存自有配置。

## 最小实现

1. 为image卡片增加严格字段`image_source = local|live`、`image_index = 0..15`。缺字段继续按旧本地模式，不能导入旧路径就自动打开文件。
2. 表单提供来源和图像索引；卡片显示来源/索引。切换本地模式时仍需用户主动载入，切换实时模式时只订阅已有缓存。
3. 抽取有界`ImageFrameStore`，按index保留payload/metadata/generation。把独立页与卡片作为消费者，不让每张卡片各自开流或各自解析协议。
4. 每次generation在GUI线程最多解码一次，复用不可变QImage/共享QPixmap；不能把QPixmap创建挪到采集线程，也不能保留指向临时Python bytes的raw指针。
5. 只解码可见且订阅的索引，最新帧合并刷新；同索引多卡片共享结果，不扩大当前32MiB载荷和1600万像素限制。另为已解码图缓存设独立总预算，不能用“每张都小于16MP”绕过总内存限制。
6. 删除/编辑/载入时解除旧订阅，清会话时清缓存，模式切换不触发任何数据发送。卡片实时图默认不套用独立页的显示滤镜/旋转，除非另有显式配置。
7. 无帧、无效格式、解码器缺失、被淘汰时显示明确占位，不继续把上一索引旧图当新结果。接收计数与实际显示帧率分开。
8. 独立文件/工作区兼容旧版，保存当前卡片来源/索引，但不保存实时payload或恢复设备连接。

## 修改范围

- 新建：`src/qx_stc_isp/vofa/image_store.py`（或同职责模块）。
- 修改：`vofa/controls.py`、`control_editor.py`、`gui.py`；必要时只扩展`images.py`的只读图像接口。
- 如新增独立设置字段，同步`workspace.py`的严格字段和兼容规则；优先使用现有卡片settings，避免无必要改变顶层版本。
- 更新功能表/指南/源码索引。不得修改STC核、协议注册、串口开关、AiCube/VOFA原文件、全局依赖或Unimate。

## 需要以后获准才运行的检查

当前用户“不再校验”仍有效，下列不是本轮执行授权：

- 同索引两卡片、不同索引、分片/坏图/淘汰、图像页与卡片同时显示、隐藏页无持续解码。
- 删除和替换卡片后的订阅清理、切换实时/本地、载入旧配置、保存/恢复、窗口拉伸/布局撤销。
- 多索引连续输入的有界内存/刷新、取消和线程退出。
- 导入、接收和布局编辑均不打开设备、不发送字节。只用合成/离线数据，不调用真实串口作为隐式夹具。

完成后仍只能说“实时图像卡片已接入且对应检查达到某层级”，不能据此声称完整VOFA等效；native工程、ABI、模型、效果参数和终端语义仍是独立任务。
