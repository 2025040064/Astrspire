"""Create a local source/evidence ZIP from an explicit allowlist, without caches."""

import argparse
import hashlib
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suffix", choices=("", "-uart-test", "-serial-assistant", "-serial-assistant-r2"), default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / f"dist/Astrspire-v0.6.0-dev4{args.suffix}-source.zip"
    if output.exists():
        raise FileExistsError("Existing source archive is preserved; choose a new version")
    entries = set()
    allowed = {
        "src": {".py", ".json"}, "tests": {".py", ".json", ".hex", ".txt"},
        "tools": {".py", ".ps1", ".js"}, "examples": {".hex", ".csv", ".json"},
        "assets/fonts": {".otf", ".ttf", ".txt", ".md"}, "docs": {".md"},
        "third_party/SerialWeb": {"", ".md", ".js"},
        "third_party/Lissio": {"", ".md"},
        "firmware/swd_capture": {".c", ".h", ".ld", ".md"},
        "firmware/g431_uart": {".c", ".h", ".md", ".txt"},
    }
    for folder, suffixes in allowed.items():
        for path in (root / folder).rglob("*"):
            if path.is_file() and path.suffix.lower() in suffixes and "__pycache__" not in path.parts:
                entries.add(path)
    for name in ("README.md", "CLAUDE.md", "pyproject.toml", "uv.lock", ".python-version", ".gitignore", "app_launcher.py"):
        path = root / name
        if path.is_file():
            entries.add(path)
    evidence = root / "build-preview/dev4"
    for name in ("full-final.xml", "full-final.log", "projection-render-final.json", "dependencies.txt",
                 "performance-before.json", "performance-after-final.json",
                 "source-final.csv", "change-summary.csv", "acceptance-source-match.json",
                 "startup-hidden-failed.json", "build-onedir.log", "build-onefile.log",
                 "build-onedir-final.log", "full-final2.log", "post-label2.log",
                 "hardware-stc-summary.json", "artifact-hashes.json"):
        path = evidence / name
        if path.is_file():
            entries.add(path)
    for pattern in ("*.xml", "*-startup.json"):
        entries.update(evidence.glob(pattern))
    for pattern in ("*.json", "*.png"):
        entries.update((evidence / "gui-preview-05").glob(pattern))
    for folder in ("loopback-60s", "loopback-600s"):
        report = evidence / folder / "result.json"
        if report.is_file():
            entries.add(report)
    if args.suffix in {"-uart-test", "-serial-assistant", "-serial-assistant-r2"}:
        entries.update((root / "build-preview/runtime-g431-uart-20261027").glob("*.xml"))
    if args.suffix == "-serial-assistant":
        serial_evidence = root / "build-preview/runtime-g431-uart-20261027"
        for name in ("xcom-build-onefile.log", "xcom-build-onedir.log"):
            entries.add(serial_evidence / name)
        entries.update((serial_evidence / "xcom-preview").glob("*.png"))
        entries.add(serial_evidence / "xcom-preview/result.json")
    if args.suffix == "-serial-assistant-r2":
        serial_evidence = root / "build-preview/runtime-g431-uart-20261027"
        for name in ("assistant-com7-rx-01.json", "assistant-r2-build-onefile.log", "assistant-r2-build-onedir.log"):
            entries.add(serial_evidence / name)
        for run in ("03", "04"):
            for name in ("receiving.png", "result.json"):
                entries.add(serial_evidence / f"assistant-gui-{run}" / name)
    for subfolder, pattern in (("preview-1", "*.png"), ("preview-1", "result.json"),
                               ("loopback-60s-final", "result.json"), ("loopback-600s", "result.json")):
        entries.update((evidence / subfolder).glob(pattern))
    manifest = []
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(entries):
            relative = path.relative_to(root).as_posix()
            if ".env" in path.parts or "private" in path.parts or path.is_symlink():
                raise ValueError(f"Unexpected source entry: {relative}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest.append(f"{digest}  {relative}")
            archive.write(path, relative)
        archive.writestr("MANIFEST.sha256", "\n".join(manifest) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Source ZIP integrity check failed")
        for line in archive.read("MANIFEST.sha256").decode().splitlines():
            digest, name = line.split("  ", 1)
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError(f"Source content mismatch: {name}")
    print(f"Created {output}: {len(entries)} files, {output.stat().st_size} bytes; CRC and SHA-256 verified")


if __name__ == "__main__":
    main()
