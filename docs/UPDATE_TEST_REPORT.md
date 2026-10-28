# Astrspire 自动更新测试记录

日期：2026-10-27
环境：Windows 11 Home China 10.0.26200 x64；Python 3.12.13；PyInstaller 6.22.3；
cryptography 50.0.2；pytest 9.1.1；Inno Setup 6.7.3（D:/tools/Inno Setup 6，winget 已安装）。
所有构建、缓存与临时产物位于 `F:\A_LIU_Astrspire\VS\51烧录`（F 盘）。测试不连接串口或板卡。

## 1. 已执行

| # | 项目 | 命令 / 方式 | 结果 |
|---|---|---|---|
| 1 | 依赖安装 | `uv add cryptography==50.0.2` | 通过；Python 3.12 Windows wheel 正常导入 |
| 2 | 更新单元测试 | `pytest tests/test_updates_core.py tests/test_updates_assistant.py tests/test_updates_ui.py` | **25 passed** |
| 3 | 完整回归 | `pytest -q --ignore-glob="tests/test_dev5_*"`（临时目录指向项目 `.pytest-tmp`） | **903 passed in 82.92s** |
| 4 | 助手打包 | PyInstaller onedir `AstrspireUpdate` | 通过；`AstrspireUpdate.exe --help` 退出码 0 |
| 5 | 启动器打包 | PyInstaller onedir `AstrspireLauncher` | 通过；`--help` 退出码 0 |
| 6 | 主程序正式布局打包 | PyInstaller onedir `Astrspire`（含版本资源、资源与字体） | 通过 |
| 7 | 健康检查 | `Astrspire.exe --health-check <file>` | 退出码 0；输出 `{"ok": true, "version": "0.7.0"}` |
| 8 | 安装版启动 | 启动打包后的 `Astrspire.exe` | 窗口标题 `Astrspire v0.7.0`，响应正常，正常关闭 |
| 9 | 文件清单 | `build_manifest()` 扫描 393 个文件并生成 `build-manifest.json` | 通过（56845 字节） |
| 10 | 助手事务成功路径 | 真实新版本目录 + 真实健康检查；安装器步骤用占位替代 | 退出码 0；`active.json` = `0.7.0`/`previous=0.6.0`；journal `done` |
| 11 | 助手拒绝篡改 | 签署后修改 `build-manifest.json` | 退出码 1；活动版本保持 `0.6.0`；journal `failed`（清单哈希不一致） |
| 12 | 稳定启动器 | `AstrspireLauncher.exe --root <root>` | 启动 `versions\0.7.0\Astrspire.exe`，标题正确，正常关闭 |
| 13 | 签名工具 | `update_signing.py keygen/sign/verify` | 通过；验证输出版本、大小与包名 |

单元测试覆盖：版本数字比较与严格三段格式、预发布过滤、旧版本不提示、跳过版本只影响自动检查、
ETag/304、403/429 退避、网络重试与退避次数、未配置公钥不联网、开发版本不参与比较、
错误签名/未知公钥/篡改清单、清单字段与资产名不一致、外部主机地址拒绝、包大小与 SHA-256 不符、
下载重试、取消、磁盘空间不足、`active.json` 原子写入与回退、事务日志往返、助手等待超时、
签名失败不切换版本、从 `verifying` 恢复、助手目录复制到程序目录之外、主窗口菜单入口存在、更新前快照可恢复且不连接设备。


## 1A. 安装器构建与更新链路（2026-10-27 晚，本轮追加）

签名：首次发布 Ed25519 密钥。私钥在仓库外 `F:/Astrspire-Secrets/update-signing.pem`，未写入源码、日志或源码包；公钥写入 `src/qx_stc_isp/updates/config.py`。Windows Authenticode 代码签名证书未配置，SmartScreen 仍可能提示未知发布者。

| 项目 | 命令 / 方式 | 结果 |
|---|---|---|
| 构建前检查 | 使用不存在的 `--key` 运行构建脚本 | 退出码 1，明确提示缺少私钥；未写出产物 |
| 一键构建 | `.venv/Scripts/python.exe tools/build_update_release.py` | 退出码 0；自动找到 ISCC 6.7.3；清单签名与包大小/SHA-256 复验通过 |
| 产物 | `build-release/output` | Setup 119352281 B，SHA-256 `954d31f02839d970d0e1905b2335bc51c202f5b8cd34ef358ac953d4b8fe12e3`；Source.zip 22086655 B，`85fe0c96454a3af51b0a8014061c02e0cc26b1c61f258c31536cc3dbeacea15c`；清单 364 B，`60a2b22e4e461b4fc68f3ece1dd166e421c138da0bfd24da14e10abac7155b1b`；签名 88 B，`07cb39a3033aa63bb9c3047092bab863480f6cef33460690c4a0da4d5971ac3b` |
| 首次安装 | Setup `/VERYSILENT /DIR=<测试目录>` | 退出码 0；`active.json` 为 `0.7.0`/previous 空；launcher 启动 `versions/0.7.0/Astrspire.exe`，标题 `Astrspire v0.7.0`，正常关闭 |
| 重装 | 同一 Setup 再次安装 | 退出码 0；`active.json` 仍为 `0.7.0`；隔离的 APPDATA/LOCALAPPDATA 文件 SHA-256 全部不变 |
| 卸载 | `unins000.exe /VERYSILENT` | 退出码 0；程序文件移除；隔离用户数据不变 |
| 中文安装目录 | `F:/安装测试/...` | 与 ASCII 安装目录结果一致（安装、重装、卸载均通过） |
| 运行数据位置 | 升级到新构建后启动应用 | `Astrspire/data/app/settings.json` 生成；升级前 `data/app/sentinel.txt` 保留；`%APPDATA%/Astrspire` 与 `%LOCALAPPDATA%/Astrspire` 均不存在 |
| 更新助手事务 | 冻结 `AstrspireUpdate.exe` + 真实 Setup + 已签名清单 | 退出码 0；清单与包验证通过；安装器完成；`build-manifest.json` 哈希匹配；健康检查 `{"ok": true, "version": "0.7.0"}`；`active.json` 切为 `0.7.0`/previous `0.6.0`；`0.6.0` 目录保留；journal `done` |

说明：本机项目目录带有工具沙箱写入的 ACL 规则，Setup 或冻结进程从该目录直接运行时可能立即退出 1 或写文件被拒；将 Setup 复制到普通目录并经资源管理器启动后全部通过。这是本机测试环境限制，不是安装包缺陷；普通用户从下载目录运行不受影响。

## 2. 未执行（明确标记，不视为通过）

| 项目 | 原因 |
|---|---|
| 0.7.0 → 0.7.1 在线升级 | 需先发布 0.7.1；本轮未创建 Release，未联网升级 |
| 文件锁定、安装失败回退、断电恢复的实机验证 | 本轮只验证成功路径；失败路径为单元级事务测试 |
| 升级前后工作区与发送历史一致性 | 安装/重装/卸载已确认隔离用户文件哈希不变；应用层快照为单元测试 |
| 干净 Windows 10/11 普通用户环境 | 未提供独立环境 |
| 公开仓库匿名端到端 | `2025040064/Astrspire-Releases` 返回 404（仓库未创建）；匿名 API 当前 403 限流；未获发布授权 |
| Windows Authenticode 代码签名 | 未购买/配置证书；与 Ed25519 更新签名是两回事 |
| 自动检查的后台提示 | 仅在冻结版启动时调度；开发测试未启动网络检查 |

## 3. 说明

- 验证签名使用临时测试密钥，已随 `build-release\verify` 清理；正式私钥未生成、未进入源码。
- 第 10 项为早期占位验证；第 1A 节已用真实安装器重跑完整事务。
- 本记录第 3 项排除了会话期间由其他进程新增的 `tests/test_dev5_*`（dev5 工作流功能，与本任务无关）。
- 先前 874 项业务测试不作为自动更新系统验收；本记录第 3 项的 903 项为最终回归总数。
- 控制台中文显示为 GBK 乱码属于终端代码页问题，写出的 JSON/日志均为 UTF-8。
