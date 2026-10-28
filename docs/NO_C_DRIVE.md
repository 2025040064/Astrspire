# C 盘写入禁令

日期：2026-10-27
适用：Claude Code 在本机进行的一切开发、构建、测试和辅助操作。

## 规则

1. **禁止在 C 盘创建或写入任何开发文件、构建产物、测试数据、临时文件、日志、缓存、快照或下载残留。**
2. 上述内容一律放在当前任务工作文件夹（本项目为 `F:\A_LIU_Astrspire\VS\51烧录`）或其子目录。
3. 需要临时目录的工具必须显式指向 F 盘：
   - Python / pytest：`TEMP`、`TMP` 指向项目内 `.pytest-tmp`；
   - PyInstaller：`PYINSTALLER_CONFIG_DIR` 指向 `build-release\pyi-cache`；
   - uv / pip 缓存、虚拟环境、浏览器下载、截图同样留在 F 盘。
4. 任务完成后删除临时文件；发布产物保留在 `build-release\output`。
5. 不因“方便”把脚本、虚拟环境、依赖缓存、日志或中间产物放到 C 盘。

## 例外（仅限与本次任务直接相关的情况）

- 用户明确指定的目标路径，例如用户要求把安装包复制到 `Downloads`。
- 系统或工具已有且无法迁移的配置目录；不得在其中新增临时产物或构建残留。

## 应用运行数据

Astrspire 的运行数据也不放 C 盘：`app_data_dir()` 与 `local_data_dir()` 默认为**安装根目录**下的 `data\app` 与 `data\local`（例如 `F:\A_LIU_Astrspire\VS\51烧录\Astrspire\data`）。设置、快照、更新下载、事务日志、更新助手副本都在这里；卸载器不删除该目录。测试仍可用 `ASTRSPIRE_APPDATA` / `ASTRSPIRE_LOCALAPPDATA` 覆盖到临时目录。

## 已落实的 F 盘设置

- 虚拟环境：`F:\A_LIU_Astrspire\VS\51烧录\.venv`
- 测试临时目录：`F:\A_LIU_Astrspire\VS\51烧录\.pytest-tmp`
- PyInstaller 缓存/工作目录：`F:\A_LIU_Astrspire\VS\51烧录\build-release\pyi-cache`、`build-release\tmp`
- 发布输出：`F:\A_LIU_Astrspire\VS\51烧录\build-release\output`
- 安装与运行数据：`F:\A_LIU_Astrspire\VS\51烧录\Astrspire`（含 `data\app`、`data\local`）

## 检查方式

操作前确认目标路径不在 C 盘；发现工具默认把缓存写到 `%LOCALAPPDATA%`、`%TEMP%`、`%USERPROFILE%` 时，先用环境变量改到 F 盘再执行。若无法避免，先说明原因并取得明确许可，不得静默写入 C 盘。
