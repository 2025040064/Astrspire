# Astrspire 发布与更新密钥指南

日期：2026-10-27
适用版本：0.7.0 起（首个安装版）
相关文档：`docs/AUTO_UPDATE.md`、`docs/AUTO_UPDATE_IMPLEMENTATION_PLAN.md`

本文只描述本机可以执行的构建、签名和验证步骤。创建仓库、上传资产、转为正式 Release 属于对外发布，必须另行取得明确授权。

## 1. 前置条件

- Windows 10/11 x64，项目虚拟环境已由 `uv sync` 建立。
- Inno Setup 6 或 7（`ISCC.exe`）。构建脚本依次查找：`ISCC` 环境变量、`PATH`、`%LOCALAPPDATA%/Programs/Inno Setup {6,7}`、`Program Files*`、`D:\tools`，以及注册表卸载项里的 `InstallLocation`。缺少时可用：
  `winget install --id JRSoftware.InnoSetup --exact`
- 发布私钥文件放在项目目录之外，默认 `F:\Astrspire-Secrets\update-signing.pem`；不得提交、不得随包、不得写入日志。
- 构建、缓存和产物全部留在 F 盘；脚本默认输出到 `build-release\output`。

## 2. 生成与保管密钥

```powershell
.venv\Scripts\python.exe tools\update_signing.py keygen --private F:\Astrspire-Secrets\update-signing.pem
```

- 只生成一次。丢失私钥后旧客户端无法验证新版本，只能人工安装并使用新公钥重建客户端。
- 打印出的 base64 公钥写入 `src/qx_stc_isp/updates/config.py` 的 `TRUSTED_PUBLIC_KEY_B64` 后重新构建。
- 私钥与公钥不匹配时，`tools/build_update_release.py` 会拒绝构建。
- 公钥为空时客户端显示“未配置更新”，不会联网检查。
- 0.7.0 首次发布已生成密钥并写入内置公钥；**不得覆盖**该私钥，否则已发布客户端的更新验证会永久失效。

### 更新签名与 Windows 代码签名的区别

| 用途 | 机制 | 现状 |
|---|---|---|
| 更新包来源与完整性 | 本项目 Ed25519 清单签名 + 内置公钥 | 已配置，构建时必须验证 |
| Windows 发布者身份 / SmartScreen | Authenticode 代码签名证书 | **未配置**；安装包未做 Windows 代码签名 |

Ed25519 通过不代表 Windows 信任发布者；首次安装仍需从确认的官方地址取得，SmartScreen 可能提示未知发布者。构建脚本会在输出中明确这一点，且没有“跳过签名验证”的开关。

## 3. 版本与构建

1. 修改 `src/qx_stc_isp/__init__.py` 的 `__version__`（三段数字，例如 `0.7.1`）与 `pyproject.toml` 的 `version`。
2. `uv lock` 更新锁文件。
3. 完整回归：

   ```powershell
   $env:TEMP='F:\A_LIU_Astrspire\VS\51烧录\.pytest-tmp'; $env:TMP=$env:TEMP
   .venv\Scripts\python.exe -m pytest -q
   ```

4. 构建、生成清单并签名：

   ```powershell
   $env:ASTRSPIRE_UPDATE_KEY='F:\Astrspire-Secrets\update-signing.pem'
   .venv\Scripts\python.exe tools\build_update_release.py --notes docs\release-notes-0.7.1.md
   ```

产物在 `build-release\output\`：

| 文件 | 说明 |
|---|---|
| `Astrspire-<version>-win-x64-Setup.exe` | Inno Setup 用户级安装器 |
| `update-win-x64.json` | 规范化清单（UTF-8、键排序、紧凑分隔符） |
| `update-win-x64.sig` | 清单原始字节的 Ed25519 签名（base64） |
| `Astrspire-<version>-win-x64-Source.zip` | 对应源码与构建材料 |
| `build-release\<version>\app\Astrspire\build-manifest.json` | 安装文件清单（其 SHA-256 绑定进签名清单） |

构建脚本最后会重新验证签名、大小与 SHA-256；不通过即失败退出。它不会访问网络，也不会创建或上传 Release。

安装器应在普通目录（如用户 Downloads）运行。若从被 ACL/沙箱策略保护的项目目录直接运行并立即以退出码 1 结束、且没有生成日志，先把 Setup.exe 复制到普通目录再运行；这是运行位置限制，不是安装包损坏。

## 4. 发布（需明确授权）

1. 在 `2025040064/Astrspire` 创建与版本一致的 Tag（例如 `v0.7.1`）和草稿 Release。此仓库已由用户批准同时托管公开源码；推送源码不等于发布 Release。
2. 上传上表中的全部资产，名称必须与清单中的 `asset` 完全一致。
3. 用另一台机器匿名下载并复核大小、SHA-256、签名：

   ```powershell
   .venv\Scripts\python.exe tools\update_signing.py verify --public <公钥base64> `
       --manifest update-win-x64.json --signature update-win-x64.sig --package Astrspire-0.7.1-win-x64-Setup.exe
   ```

4. 获得批准后转为正式发布。草稿或预发布不会被客户端识别为 stable。

正式发布前门禁（与实施方案一致）：

- SerialWeb 移植的 AGPL 义务：Release 必须同时提供对应完整源码与构建材料。
- 保留 Lissio 及其他依赖许可。
- 随包字体授权材料尚未确认前，不得公开发布；不得擅自删除或替换字体。
- 免费 Ed25519 签名不能消除 SmartScreen 提示；首次安装包必须从确认的官方地址取得。

## 5. 失败与恢复

- 安装或健康检查失败时，`active.json` 保持旧版本；`update-journal.json` 记录 `failed` 与原因，日志在 `<install_root>/data/local/logs/update.log`。
- 断电或助手退出：下一次通过 `AstrspireLauncher.exe` 启动会先调用 `AstrspireUpdate.exe --resume`，从 `waiting/installing/verifying` 阶段继续；失败则仍启动旧活动版本。
- 手工回退：编辑 `<install_root>/active.json` 的 `version` 为上一版本目录名（保留 `previous`），再启动 `AstrspireLauncher.exe`。不要删除仍在使用的版本目录。
- 安装包或清单损坏时助手拒绝安装，不覆盖旧版本；重新下载即可。
- 更新助手最低版本不满足时客户端提示手动安装，不自动执行。

## 6. 数据位置

- 更新前快照：`<install_root>/data/app/recovery/`（工作区、20 条发送历史、校验清单）。
- 下载与事务：`<install_root>/data/local/updates/`、`update-journal.json`。
- 用户自行保存的工作区、CSV、JSONL、固件保持原路径，更新不移动、不改格式。
