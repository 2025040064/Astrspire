"""Check Git's staged source snapshot without logging matching secret values."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(*arguments):
    return subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}", *arguments],
                          cwd=ROOT, capture_output=True, check=True).stdout


def main():
    paths = git("ls-files", "-z").decode("utf-8").split("\0")
    paths = [path for path in paths if path]
    forbidden_roots = ("Astrspire/", "data/", "captures/", ".venv/", "build/", "build-release/", "build-preview/", "dist/")
    forbidden_suffixes = {".pem", ".key", ".pfx", ".p12", ".der", ".exe", ".dll", ".pyd", ".zip", ".ttf", ".otf"}
    patterns = {"github_token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
                "private_key": re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\s*\r?\n[A-Za-z0-9+/]{20,}")}
    findings = []
    total_bytes = 0
    for path in paths:
        name = Path(path).name
        if path.startswith(forbidden_roots) or name == ".env" or name.startswith(".env.") or Path(path).suffix.lower() in forbidden_suffixes:
            findings.append({"path": path, "rule": "local_or_private_file"})
        data = git("show", ":" + path)
        total_bytes += len(data)
        if len(data) > 50 * 1024 * 1024:
            findings.append({"path": path, "rule": "oversized_git_blob"})
        for rule, pattern in patterns.items():
            if pattern.search(data):
                findings.append({"path": path, "rule": rule})
    required = {"installer/Astrspire.iss", "uv.lock", "examples/led.hex", "tests/test_updates_core.py",
                "third_party/SerialWeb/LICENSE", "third_party/Lissio/LICENSE"}
    for path in sorted(required - set(paths)):
        findings.append({"path": path, "rule": "required_source_missing"})
    report = {"date": "2026-10-28", "files": len(paths), "source_bytes": total_bytes,
              "findings": findings, "scope": "staged paths, common GitHub credentials, PEM private keys; not a formal security audit"}
    target = ROOT / "docs/maintenance/publication-audit-2026-10-28.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(paths), "bytes": total_bytes, "findings": findings}, ensure_ascii=False))
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
