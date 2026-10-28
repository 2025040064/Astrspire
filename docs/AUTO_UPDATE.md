# Astrspire 自动更新架构与实施记录

日期：2026-10-27
来源方案：`docs/AUTO_UPDATE_IMPLEMENTATION_PLAN.md`
状态：实施中；本文记录实际落地结构、边界与未执行项，不把未测试内容写成已通过。

## 1. 技术栈影响

| 项 | 变化 | 影响 |
|---|---|---|
| 依赖 | `cryptography==50.0.2` | 已在本机 Python 3.12.13 安装并写入 `pyproject.toml`／`uv.lock`；仅更新模块使用 Ed25519，不自写密码算法 |
| 打包 | 新增 onedir 正式渠道 | 旧 onefile／onedir 便携构建保留，不改现有入口 `app_launcher.py` |
| 安装 | Inno Setup 6 | 用户级安装，`PrivilegesRequired=lowest`、`CloseApplications=no`、`RestartApplications=no`，以 `AppMutex` 阻止运行期间安装 |
| 版本来源 | `src/qx_stc_isp/__init__.py:__version__` | 构建名、安装器、manifest、Tag 均由此生成；不再硬编码在构建脚本 |
| 退出 | 更新前保存工作区快照与 20 条发送历史 | 快照写入 `%APPDATA%\Astrspire\recovery`，恢复时只还原配置，不连接设备、不发送、不烧录 |
| 业务代码 | 仅新增菜单项与快照出口 | 串口、STC 烧录、波形、协议逻辑不变；schema8 不升级 |

## 2. 目录与信任布局

本机安装根：`F:\A_LIU_Astrspire\VS\51烧录\Astrspire`（用户级，不请求管理员权限）。运行数据跟随用户选择的安装根，不默认写入 `%APPDATA%` / `%LOCALAPPDATA%`。

首次安装默认建议安装包旁的 `Astrspire` 子目录；安装向导仍可选择位置，已安装时优先保留原目录。更新助手使用明确的 `/DIR`，不会随下载包所在目录改变已有安装位置。源仓库与更新源统一为 `2025040064/Astrspire`；尚未发布 Release 时不能完成在线升级。

桌面和开始菜单快捷方式均改为默认不勾选，只有用户主动选择才创建；本机覆盖安装明确关闭这两项。

```text
Astrspire\
  AstrspireLauncher.exe        # 稳定启动入口，桌面快捷方式只指向它
  active.json                  # {"version":"0.7.1","previous":"0.7.0"}，原子替换
  versions\
    0.7.0\Astrspire.exe ...    # PyInstaller onedir
    0.7.1\Astrspire.exe ...
  uninstall\...
```

运行数据全部在安装根目录的 `data/` 下（不在 C 盘）：

- `<install_root>/data/app/settings.json`：更新偏好（自动检查、自动下载、跳过版本、ETag、退避时间）。
- `<install_root>/data/app/recovery/workspace.json`、`send-history.json`、`recovery.json`：更新前快照；可通过“工具 → 恢复更新前工作区…”还原。
- `<install_root>/data/local/updates/<version>/*`：`.partial` 下载与最终安装包。
- `<install_root>/data/local/update-journal.json`：可恢复的更新事务。
- `<install_root>/data/local/updater/`：更新助手运行目录（不覆盖自身）。
- 日志：`<install_root>/data/local/logs`；用户选择的 CSV／JSONL／固件保持原路径。
- 测试可用 `ASTRSPIRE_APPDATA` / `ASTRSPIRE_LOCALAPPDATA` 覆盖到临时目录。

旧便携版保留原样，不自动迁移；只提供一次手动安装新版的路径。安装新版不回写旧目录。

## 3. 更新模块（`src/qx_stc_isp/updates/`）

| 模块 | 职责 | 边界 |
|---|---|---|
| `config.py` | 应用标识、固定仓库、下载前缀、可信公钥、路径与偏好 | 无网络；公钥为空时状态为“未配置” |
| `manifest.py` | 清单解析/校验、版本比较、Ed25519 验签、`VerifiedUpdate` | 只返回验证通过的对象；不接受网络下发公钥 |
| `http.py` | urllib HTTPS 客户端、重定向落地域名校验、流式下载 | 保持系统证书校验；不执行任意 URL |
| `checker.py` | 匿名读取 latest release、ETag/304、429/403 退避、超时重试、下载与取消 | 不阻塞启动；失败转错误状态 |
| `state.py` | 版本目录、`active.json` 原子写入、事务日志 | 先写临时文件再 `os.replace` |
| `assistant.py` | 等待实例退出、验证、静默安装、健康检查、切换活动版本、恢复 | 不 taskkill／不 TerminateProcess；失败保留旧版本 |
| `launcher.py` | 读活动记录启动对应版本；未完成事务先恢复 | 不进入业务模块 |
| `process.py` | 应用实例命名互斥量、等待互斥量消失 | 只在 Windows 生效；测试环境直接返回 |
| `service.py` | Qt 工作线程封装检查/下载/应用更新 | 联网不在 GUI 线程 |
| `dialog.py` | 检查更新/设置/进度/更新说明 | 不重排主窗口 |

命名对应：实施中的 `UpdateService` 即方案里的 `UpdateController`；`UpdateCoordinator` 的协调职责由 `state`（版本记录）、`process`（实例互斥）、`assistant`（安装/恢复）共同承担。启动器在未完成事务恢复前不启动业务界面；恢复等待期间不另开进度窗口。

信任链固定顺序：

1. 用客户端内置公钥验证清单原始字节（先验签，后解析）。
2. 校验清单字段：应用标识、stable 通道、win-x64、三段版本号、资产名。
3. 校验安装包大小与 SHA-256。
4. 更新助手在执行安装器前重复 1–3，并校验安装后文件清单哈希。

签名说明：Ed25519 只证明发布密钥身份，**不消除 SmartScreen 提示**，也不让 Windows 显示已验证证书发布者。首次安装包须从确认的官方发布地址取得。

## 4. 更新流程

```text
检查 → 下载（.partial，可取消）→ 验签/大小/SHA-256 → VerifiedUpdate
  → 用户选“立即重启更新”/“稍后更新”
  → 保存工作区与20条发送历史 → 写事务日志（waiting）→ 启动助手 → 正常退出
  → 助手等待应用互斥量消失 → 静默安装到 versions\<new>
  → 校验文件清单 → 新版本 --health-check（不连接设备）→ 写 active.json → 清事务
  → 启动稳定入口（仅“立即重启更新”）
```

门禁：

- STC 擦写进行中只允许检查和下载；更新应用被拒绝并提示。
- 工作区快照失败或用户取消 → 不退出、不安装。
- 多实例：助手等到全部实例的命名互斥量消失才安装；超时则保留事务、不安装。
- 健康检查进程未正常退出时不强杀，不提交新版本，事务保留为失败状态。
- 新实例启动发现未完成事务时由启动器先调用助手恢复；恢复失败仍启动旧活动版本并保留错误日志。

## 5. 发布产物与工具

每个版本：

- `Astrspire-<version>-win-x64-Setup.exe`
- `update-win-x64.json` / `update-win-x64.sig`
- `Astrspire-<version>-win-x64-Source.zip`（AGPL 对应源码）
- `build-manifest.json`（源码快照哈希、依赖锁、工具版本、产物哈希）

工具：

- `tools/update_signing.py`：生成 Ed25519 密钥对、签名清单、验证签名。私钥路径由发布方指定，不写入源码目录。
- `tools/build_update_release.py`：构建前检查 Python/PyInstaller/cryptography/ISCC/私钥/内置公钥，再构建 onedir、助手、启动器 → 调用 ISCC 生成安装器 → 生成并签名清单 → 验证并输出到 `build-release\output`。缺少任何依赖即失败，没有跳过签名验证的开关。
- `installer/Astrspire.iss`：Inno Setup 6/7 用户级脚本；安装到 `versions\<version>`，安装结束时写 `active.json`（首次或手工升级），检测到助手的 `waiting/installing/verifying` 事务时不写，交给健康检查通过的助手切换。
- `ISCC` 查找顺序：`ISCC` 环境变量 → PATH → `%LOCALAPPDATA%\Programs\Inno Setup {6,7}` → `Program Files*` → 注册表 `InstallLocation`。

发布脚本默认只构建和验证；创建仓库、上传、正式发布需另行授权。

## 6. 测试与当前状态

离线单元测试（26 项）覆盖：版本比较、预发布过滤、跳过版本、ETag/304、限流、重试、取消、下载重试、空间不足、损坏包、错误签名、未知公钥、清单与资产不一致、错误平台、降级、运行数据落在安装根 data 下、active.json 原子写入与无记录时回退最新版本、事务恢复、快照写入与恢复、主窗口入口。

已实现但存在一处偏离：urllib 只有单一 socket 超时，连接/读取未拆分为 5 秒/15 秒，当前统一 15 秒，代码中以 `shortcut:` 标注。

未执行（缺环境或未获授权）：

- Inno Setup 编译与安装/升级测试（本机未安装 Inno Setup）。
- 公开仓库匿名端到端测试（未创建仓库、未发布）。
- 真实工作区/发送历史的升级前后一致性（需要安装版环境）。

未配置可信公钥时，客户端显示“未配置更新”，不下载、不安装。

## 7. 已完成/后续

- [x] `cryptography==50.0.2` 安装与依赖锁更新（Python 3.12 Windows wheel）。
- [x] 更新核心模块与离线测试。
- [x] 主窗口“检查更新”入口、自动检查、更新前快照与“恢复更新前工作区”入口。
- [x] 更新助手、稳定启动器、事务与活动版本切换。
- [x] 签名/构建工具与 Inno Setup 脚本。
- [x] 首次发布 Ed25519 密钥与内置公钥（私钥在仓库外 `F:/Astrspire-Secrets/update-signing.pem`）。
- [x] 本机构建 `0.7.0` 安装器并完成安装/重装/卸载、启动与用户数据保留测试。
- [x] 真实安装器 + 助手事务（验签、安装、健康检查、切换 active.json）本地验证。
- [ ] 0.7.0 → 0.7.1 网络升级与公开仓库匿名端到端（仓库未创建、未发布、GitHub 网络受限）。
