# Lissio 来源与修改记录

Copyright (C) 2026 Horldsence and Lissio contributors.

当前所参考的源码使用 Apache License 2.0，完整许可见同目录 LICENSE。
原项目：https://codeberg.org/lissio-industries/Lissio

固定提交：总仓库 8e126c3af2663cf0b2dd895dab6aad3a4efe06ad；Client 316b02f1dd69014555828624f521753e7e6aaa2c；Server deec32ce1854baafdff254577eb8a46a6a2456bc；Core 559d4a466bffe9e7ee2788730e9675ac84de6a12。

## 实际复用

- `src/qx_stc_isp/vofa/protocol.py`：从 Core 的 `crates/protocol/device_profile/src/frame/checksum.rs` 适配命名 CRC 算法为 Python；CRC32采用Python标准库zlib，增加显式覆盖范围、独立发送模板和本工程流式解析器。
- Client 的 RawDataRow、rawDataSend、useSendHistory 与 Server 的 preview.rs 用作交互/边界参考；Qt界面与数据模型按本工程独立实现，不嵌入React/Tauri/Rust后台。
- 不复制字体、标志、图像、AI、CAN、工业协议或上游依赖树。

适配日期：2026-10-27。上游测试源码仅作参考；本工程测试与实板结果分别记录。当前许可不追溯改变上游旧二进制或本工程已有 SerialWeb AGPL-3.0-only 来源义务。
