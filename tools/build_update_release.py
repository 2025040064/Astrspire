"""Build and verify a release locally. Never creates repositories or uploads."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from qx_stc_isp.updates.config import (  # noqa: E402
    APP_ID, CHANNEL, PLATFORM, TRUSTED_PUBLIC_KEY_B64, asset_name, source_name,
)

ISCC_ENV = "ISCC"
DEFAULT_KEY_PATH = Path(r"F:\Astrspire-Secrets\update-signing.pem")
CODE_SIGNING_NOTE = (
    "Ed25519 更新签名只验证发布密钥；它不等于 Windows 代码签名证书。"
    "安装包未做 Authenticode 签名，SmartScreen 仍可能提示未知发布者。"
)

ASSET_ARGS = [
    "--add-data", f"{ROOT / 'third_party' / 'SerialWeb'};third_party/SerialWeb",
    "--add-data", f"{ROOT / 'third_party' / 'Lissio'};third_party/Lissio",
    "--add-data", f"{ROOT / 'docs' / 'SERIAL_TOOLS_DEV4.md'};docs",
    "--add-data", f"{ROOT / 'docs' / 'VOFA_WORKBENCH_GUIDE.md'};docs",
    "--add-data", f"{ROOT / 'docs' / 'AUTO_UPDATE.md'};docs",
    "--add-data", f"{ROOT / 'examples' / 'led.hex'};examples",
    "--add-data", f"{ROOT / 'examples' / 'flashled.hex'};examples",
    "--add-data", f"{ROOT / 'examples' / 'telemetry_matrix.csv'};examples",
    "--add-data", f"{ROOT / 'examples' / 'engine_temperature.json'};examples",
    "--add-data", f"{ROOT / 'assets' / 'fonts'};assets/fonts",
]


def read_version() -> str:
    text = (ROOT / "src" / "qx_stc_isp" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise SystemExit("无法从 src/qx_stc_isp/__init__.py 读取版本号")
    version = match.group(1)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"稳定版版本号必须为三段数字：{version}")
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    environment = dict(os.environ)
    system_root = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join(str(path) for path in (
        ROOT / ".venv" / "Scripts", Path(sys.base_prefix),
        system_root / "System32", system_root, system_root / "System32" / "Wbem",
    ))
    # PyInstaller must not bundle an unrelated tool's Qt/ICU/OpenSSL DLLs.
    for name in ("PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH"):
        environment.pop(name, None)
    temporary = ROOT / "build-release" / "tmp"
    cache = ROOT / "build-release" / "pyi-cache"
    temporary.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    environment.update(TEMP=str(temporary), TMP=str(temporary),
                       PYINSTALLER_CONFIG_DIR=str(cache), PYTHONDONTWRITEBYTECODE="1")
    subprocess.run(command, check=True, cwd=ROOT, env=environment)


def write_version_resource(path: Path, version: str, description: str) -> Path:
    major, minor, patch = version.split(".")
    template = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Astrspire'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{description}'),
      StringStruct('OriginalFilename', '{description}.exe'),
      StringStruct('ProductName', 'Astrspire'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""
    path.write_text(template, encoding="utf-8")
    return path


def pyinstaller(name: str, entry: Path, dist: Path, work: Path, spec: Path, extra: list[str]) -> Path:
    run([
        str(PYTHON), "-m", "PyInstaller", "--onedir", "--noconfirm", "--clean",
        "--name", name, "--paths", str(ROOT / "src"),
        "--distpath", str(dist), "--workpath", str(work), "--specpath", str(spec),
        *extra, *ASSET_ARGS, str(entry),
    ])
    return dist / name


def build_manifest(version_root: Path, version: str) -> Path:
    """Hash the installed file set; the signed manifest binds this file's hash."""

    files = []
    for path in sorted(version_root.rglob("*")):
        if path.is_file() and path.name != "build-manifest.json":
            files.append({
                "path": path.relative_to(version_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    source = hashlib.sha256()
    for path in sorted((ROOT / "src").rglob("*.py")):
        source.update(path.relative_to(ROOT).as_posix().encode())
        source.update(bytes.fromhex(sha256_file(path)))
    document = {
        "app_id": APP_ID,
        "version": version,
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pyinstaller": subprocess.run(
            [str(PYTHON), "-m", "PyInstaller", "--version"], capture_output=True, text=True, check=True,
        ).stdout.strip(),
        "dependency_lock_sha256": sha256_file(ROOT / "uv.lock"),
        "source_sha256": source.hexdigest(),
        "files": files,
    }
    target = version_root / "build-manifest.json"
    target.write_bytes(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    return target


def find_iscc() -> Path:
    """Locate ISCC.exe from Inno Setup 6 or 7 without assuming one install path."""

    candidates = [os.environ.get(ISCC_ENV, ""), shutil.which("iscc") or ""]
    candidates += [
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / f"Inno Setup {major}" / "ISCC.exe")
        for major in (6, 7)
    ]
    for major in (6, 7):
        candidates += [
            rf"C:\Program Files (x86)\Inno Setup {major}\ISCC.exe",
            rf"C:\Program Files\Inno Setup {major}\ISCC.exe",
            rf"D:\tools\Inno Setup {major}\ISCC.exe",
        ]
    candidates += _registry_iscc_paths()
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit(
        "未找到 Inno Setup 编译器 ISCC.exe（支持 6/7）。\n"
        "请先安装：winget install --id JRSoftware.InnoSetup --exact\n"
        "或设置 ISCC 环境变量指向 ISCC.exe。"
    )


def _registry_iscc_paths() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    results: list[str] = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                parent = winreg.OpenKey(root, uninstall, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(parent, name) as item:
                            display = str(winreg.QueryValueEx(item, "DisplayName")[0])
                            if "Inno Setup" not in display:
                                continue
                            location = str(winreg.QueryValueEx(item, "InstallLocation")[0]).strip()
                    except OSError:
                        continue
                    if location:
                        candidate = Path(location) / "ISCC.exe"
                        if candidate.is_file() and str(candidate) not in results:
                            results.append(str(candidate))
            finally:
                parent.Close()
    return results


def iscc_major(iscc: Path) -> int | None:
    result = subprocess.run([str(iscc), "/?"], capture_output=True, text=True, errors="replace")
    match = re.search(r"Inno Setup (\d+)", result.stdout + result.stderr)
    return int(match.group(1)) if match else None


def check_prerequisites(key: Path) -> Path:
    """Fail before building with actionable messages; never skip verification."""

    problems: list[str] = []
    if not PYTHON.is_file():
        problems.append(f"缺少项目虚拟环境：{PYTHON}（先运行 uv sync）")
    for relative in ("uv.lock", "installer/Astrspire.iss", "src/qx_stc_isp/__init__.py",
                     "assets/fonts", "third_party/SerialWeb"):
        if not (ROOT / relative).exists():
            problems.append(f"缺少构建输入：{relative}")
    for module in ("PyInstaller", "cryptography"):
        probe = subprocess.run([str(PYTHON), "-c", f"import {module}"], capture_output=True, text=True)
        if probe.returncode != 0:
            problems.append(f"缺少 Python 依赖 {module}（先运行 uv sync）")
    if not TRUSTED_PUBLIC_KEY_B64:
        problems.append(
            "客户端内置更新公钥为空：运行 tools/update_signing.py keygen，"
            "把输出的公钥写入 src/qx_stc_isp/updates/config.py 的 TRUSTED_PUBLIC_KEY_B64"
        )
    if not key or not key.is_file():
        problems.append(
            "缺少发布私钥：使用 --key 或 ASTRSPIRE_UPDATE_KEY 指向 Ed25519 私钥 PEM；"
            "首次发布可运行 tools/update_signing.py keygen 生成（不得放入仓库）"
        )
    iscc = None
    try:
        iscc = find_iscc()
    except SystemExit as exc:
        problems.append(str(exc))
    if iscc is not None:
        major = iscc_major(iscc)
        if major not in (6, 7):
            problems.append(f"ISCC 版本不受支持（检测到 {major}）：{iscc}")
    if problems:
        raise SystemExit("构建前检查失败：\n- " + "\n- ".join(problems))
    print(f"构建前检查通过：Python {sys.version.split()[0]}，ISCC {iscc}，私钥 {key}")
    return iscc



def write_source_zip(target: Path, version: str) -> None:
    include = ["src", "tests", "examples", "firmware", "third_party", "installer", "tools", "docs", "assets",
               "pyproject.toml", "uv.lock", "README.md", "app_launcher.py", ".python-version", ".gitignore", "CLAUDE.md"]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in include:
            path = ROOT / name
            paths = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
            for item in paths:
                if "__pycache__" in item.parts or item.suffix.lower() in (
                    ".pyc", ".zip", ".exe", ".dll", ".pyd", ".pem", ".key", ".pfx", ".p12", ".ttf", ".otf",
                ) or item.name == ".env" or item.name.startswith(".env."):
                    continue
                archive.write(item, f"{source_name(version)}/{item.relative_to(ROOT).as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_update_release")
    parser.add_argument("--key", type=Path, default=Path(os.environ.get("ASTRSPIRE_UPDATE_KEY") or DEFAULT_KEY_PATH))
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build-release" / "output")
    args = parser.parse_args(argv)

    version = read_version()
    iscc = check_prerequisites(args.key)
    from update_signing import load_private, sign as sign_manifest, verify as verify_signature

    public_b64 = base64.b64encode(
        load_private(args.key).public_key().public_bytes_raw()
    ).decode("ascii")
    if public_b64 != TRUSTED_PUBLIC_KEY_B64:
        raise SystemExit("发布私钥与客户端内置公钥不匹配；拒绝构建不一致的发布包。")
    print("更新签名：Ed25519 私钥与内置公钥匹配。")
    print(CODE_SIGNING_NOTE)

    staging = ROOT / "build-release" / version
    for name in ("work", "spec"):
        shutil.rmtree(staging / name, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    version_resource = write_version_resource(staging / "version-app.txt", version, "Astrspire")
    launcher_resource = write_version_resource(staging / "version-laucher.txt", version, "AstrspireLauncher")
    assistant_resource = write_version_resource(staging / "version-update.txt", version, "AstrspireUpdate")

    app_dir = pyinstaller(
        "Astrspire", ROOT / "app_launcher.py", staging / "app", staging / "work" / "app",
        staging / "spec",
        ["--windowed", "--copy-metadata", "pyqtgraph", "--copy-metadata", "numpy",
         "--version-file", str(version_resource)],
    )
    launcher_dir = pyinstaller(
        "AstrspireLauncher", ROOT / "tools" / "launcher_entry.py", staging / "launcher",
        staging / "work" / "launcher", staging / "spec",
        ["--contents-directory", "launcher-internal", "--version-file", str(launcher_resource)],
    )
    assistant_dir = pyinstaller(
        "AstrspireUpdate", ROOT / "tools" / "update_assistant_entry.py", staging / "assistant",
        staging / "work" / "assistant", staging / "spec",
        ["--version-file", str(assistant_resource)],
    )

    manifest_file = build_manifest(app_dir, version)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    run([
        str(iscc),
        f"/DAppVersion={version}",
        f"/DAppSource={app_dir}",
        f"/DLauncherSource={launcher_dir}",
        f"/DAssistantSource={assistant_dir}",
        f"/DOutputDir={output}",
        str(ROOT / "installer" / "Astrspire.iss"),
    ])
    setup = output / f"{asset_name(version)}"
    if not setup.is_file():
        raise SystemExit(f"安装器未生成：{setup}")

    notes = args.notes.read_text(encoding="utf-8") if args.notes else f"Astrspire {version}"
    document = {
        "app_id": APP_ID,
        "channel": CHANNEL,
        "version": version,
        "platform": PLATFORM,
        "asset": setup.name,
        "size": setup.stat().st_size,
        "sha256": sha256_file(setup),
        "files_sha256": sha256_file(manifest_file),
        "min_assistant": 1,
        "notes": notes,
    }
    manifest_path = output / "update-win-x64.json"
    manifest_path.write_bytes(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    signature_path = output / "update-win-x64.sig"
    sign_manifest(args.key, manifest_path, signature_path)
    verify_signature(public_b64, manifest_path, signature_path, setup)
    write_source_zip(output / source_name(version), version)

    print("\n构建完成（未上传、未发布）：")
    for item in sorted(output.iterdir()):
        print(f"  {item.name}  {item.stat().st_size} bytes  {sha256_file(item)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
