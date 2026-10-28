"""Archive private hardware evidence separately from the shareable source ZIP."""
import hashlib
from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    runtime = root / "build-preview/runtime-v060-dev3"
    output = root / "dist/Astrspire-v0.6.0-dev3-private-hardware.zip"
    entries = set()
    for name in ("f103-before.bin", "f103-confirm.bin"):
        entries.add(runtime / name)
    for folder in ("firmware", "f103-attach", "f103-experiment-1", "f103-experiment-2",
                   "f103-experiment-3", "f103-gui-experiment", "stc-capture"):
        for path in (runtime / folder).rglob("*"):
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl", ".bin", ".elf", ".hex", ".csv", ".png", ".log", ".cfg"}:
                if path.is_symlink():
                    raise ValueError("Private archive cannot follow symlinks")
                entries.add(path)
    hashes = []
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("PRIVATE-DO-NOT-PUBLISH.txt", "Private firmware backups, device identifiers and raw captures. Do not upload this archive to a public repository.\n")
        for path in sorted(entries):
            relative = path.relative_to(root).as_posix()
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            archive.write(path, relative)
            hashes.append(f"{digest}  {relative}")
        archive.writestr("MANIFEST.sha256", "\n".join(hashes) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise ValueError("Private archive CRC failed")
        for line in archive.read("MANIFEST.sha256").decode().splitlines():
            digest, name = line.split("  ", 1)
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("Private archive SHA-256 failed")
    print(f"Private archive verified: {len(entries)} files, {output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
