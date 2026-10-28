"""Summarize existing evidence without connecting hardware or rewriting reports."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "build-preview/dev4"
OLD_TIP = "仅本次有效。默认按文件范围擦除；不修改EEPROM、ISP或选项。全区擦除尚未完成实板复测。"
NEW_TIP = "仅本次有效。默认按文件范围擦除；不修改EEPROM、ISP或选项。8KiB擦除ACK已实板复测，未做全区空白回读。"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(name, value):
    with (EVIDENCE / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    sources = {str(p.relative_to(ROOT)): p.read_bytes() for p in (ROOT / "src").rglob("*.py")}
    matching = []
    for folder in ("loopback-60s", "loopback-600s", "gui-preview-05"):
        report = json.loads((EVIDENCE / folder / "result.json").read_text(encoding="utf-8"))
        changes = []
        for name, previous in report["source_sha256"].items():
            path = (ROOT / name).resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError("Evidence source path escaped the project")
            current = path.read_bytes()
            if digest(current) == previous:
                continue
            if (name.replace("\\", "/") == "src/qx_stc_isp/gui.py"
                    and current.count(NEW_TIP.encode()) == 1
                    and digest(current.replace(NEW_TIP.encode(), OLD_TIP.encode())) == previous):
                changes.append({"file": name, "scope": "Only the STC erase verification tooltip changed"})
            else:
                raise ValueError(f"Unverified source change after {folder}: {name}")
        if folder.startswith("gui-") and set(report["source_sha256"]) != set(sources):
            raise ValueError("Source inventory changed")
        if not report["passed"]:
            raise ValueError(f"Acceptance did not pass: {folder}")
        matching.append({"report": folder + "/result.json", "passed": True,
                         "tracked_file_count": len(report["source_sha256"]),
                         "exact_source_match": not changes, "verified_presentation_changes": changes})
    save("acceptance-source-match.json", matching)
    hardware = []
    for operation in ("erase", "download"):
        capture = ROOT / f"captures/private/dev4-{operation}-20261027-01.jsonl"
        records = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
        result = records[-1]["result"]
        if result["state"] != "COMPLETE" or not result["erase_ack_received"]:
            raise ValueError(f"Hardware operation failed: {operation}")
        hardware.append({"operation": operation, "started": records[0]["started"],
                         "adapter": {key: records[0]["device"][key] for key in ("device", "vid", "pid")},
                         "raw_capture_sha256": digest(capture.read_bytes()), "result": result})
    save("hardware-stc-summary.json", hardware)
    artifacts = []
    for path in (ROOT / "dist/Astrspire-v0.6.0-dev4.exe",
                 ROOT / "dist/Astrspire-v0.6.0-dev4/Astrspire-v0.6.0-dev4.exe"):
        artifacts.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
                          "sha256": digest(path.read_bytes())})
    save("artifact-hashes.json", artifacts)
    print(json.dumps({"acceptance_reports": len(matching), "hardware_operations": len(hardware),
                      "artifacts": len(artifacts), "passed": True}))


if __name__ == "__main__":
    main()
