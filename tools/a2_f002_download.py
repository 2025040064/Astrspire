"""A2 实验入口：F002 / 6.6C 最小真实下载（默认只做预检查，不擦写）。

用法（在工程根目录）：

    # 预检查：识别、校验固件范围、计算波特率计划；不进入协商/擦除。
    .venv\\Scripts\\python.exe tools\\a2_f002_download.py --port COM8 --phase precheck

    # 真实写入：必须显式给出确认短语；会擦除芯片现有程序。
    .venv\\Scripts\\python.exe tools\\a2_f002_download.py --port COM8 --phase write \
        --confirm-erase "ERASE F002 66-43-FD"

本工具只复用已存在的 Stc89DownloadSession 与端口租约，不新增第二套协议。
原始 TX/RX 写入 captures/private/，不打入发行包。块 ACK 不代表全片回读，
FF/82 结束没有已知应答；完成后必须手动断电重启验证运行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qx_stc_isp.hex_image import HexImage  # noqa: E402
from qx_stc_isp.port_lease import PORT_LEASES  # noqa: E402
from qx_stc_isp.ports import enumerate_ports  # noqa: E402
from qx_stc_isp.session import SessionState  # noqa: E402
from qx_stc_isp.stc89 import BLOCK_BYTES, ERASE_BYTES, PROGRAM_BYTES  # noqa: E402
from qx_stc_isp.stc89_session import Stc89DownloadSession  # noqa: E402
from qx_stc_isp.transport import PySerialTransport  # noqa: E402

EXPECTED_FIRMWARE = ROOT / "examples" / "led.hex"
EXPECTED_FIRMWARE_SHA256 = "8DF1C8ED7E875F067D0280FED35B3B072FD06201C722CEB4288F37CAE618BA3B"
EXPECTED_TARGET = "F002:66-43-FD"
CONFIRM_PHRASE = "ERASE F002 66-43-FD"
AICUBE = Path(r"C:\Users\Legion\Desktop\AiCube-ISP-v6.97A.exe")
AICUBE_SHA256 = "6194B42B9F7812E745AB19E6F5A25B474F740F84C7AC9E7CFA940CBBBFE1CBCC"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def check_recovery_tool() -> str:
    if not AICUBE.is_file():
        return f"未找到恢复工具：{AICUBE}"
    try:
        actual = sha256(AICUBE)
    except OSError as exc:
        return f"恢复工具无法读取（{type(exc).__name__}），本次不能用该文件恢复"
    if actual != AICUBE_SHA256:
        return f"恢复工具哈希不匹配：{actual}（期望 {AICUBE_SHA256}）"
    return "恢复工具 AiCube v6.97A 哈希匹配"


def check_firmware(path: Path) -> HexImage:
    if not path.is_file():
        raise SystemExit(f"固件不存在：{path}")
    actual = sha256(path)
    if actual != EXPECTED_FIRMWARE_SHA256:
        raise SystemExit(f"A2 只允许同一 led.hex：{path} 的 SHA-256 为 {actual}")
    image = HexImage.from_file(path)
    if image.min_address < 0 or image.max_address >= PROGRAM_BYTES:
        raise SystemExit(f"固件超出 F002 的 {PROGRAM_BYTES} 字节程序区")
    return image


def make_logger(path: Path, header: dict[str, object]):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("w", encoding="utf-8")
    stream.write(json.dumps({"kind": "A2_HEADER", "at": datetime.now().astimezone().isoformat(),
                             **header}, ensure_ascii=False) + "\n")
    stream.flush()

    def emit(event) -> None:
        if event.kind in ("tx", "rx") and event.data:
            line = f"{event.direction or event.kind.upper()} {event.data.hex(' ').upper()}"
            print(f"  {line}", flush=True)
        elif event.message:
            print(f"  {event.message}", flush=True)
        stream.write(json.dumps({
            "at": datetime.now().astimezone().isoformat(),
            "kind": event.kind,
            "state": getattr(event.state, "value", str(event.state)),
            "direction": event.direction,
            "data": event.data.hex() if event.data else None,
            "progress": event.progress,
            "message": event.message,
        }, ensure_ascii=False) + "\n")
        stream.flush()

    return stream, emit


def describe(session: Stc89DownloadSession, image: HexImage) -> list[str]:
    lines: list[str] = []
    status = session.status
    if status is not None:
        lines.append(f"芯片 ID 0x{status.chip_id:04X}；版本 {status.version_raw.hex(' ').upper()}（{status.version_label}）")
        lines.append(f"时钟样本 {status.timing_samples}；状态包 {status.payload_size} 字节")
    plan = session.baud_plan
    if plan is not None:
        lines.append(f"时钟 {plan.clock_hz} Hz；目标 {plan.baudrate}；参数 {plan.parameters.probe().hex(' ').upper()}")
    end = (image.max_address // ERASE_BYTES + 1) * ERASE_BYTES
    lines.append(f"固件 {image.data_size} 字节，地址 0x{image.min_address:04X}–0x{image.max_address:04X}")
    lines.append(f"擦除/写入范围 0x0000–0x{end - 1:04X}；{end // BLOCK_BYTES} 个 {BLOCK_BYTES} 字节块")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A2 F002/6.6C 最小真实下载实验入口")
    parser.add_argument("--port", required=True, help="显式串口，例如 COM8")
    parser.add_argument("--firmware", type=Path, default=EXPECTED_FIRMWARE)
    parser.add_argument("--phase", choices=("precheck", "write"), default="precheck")
    parser.add_argument("--target", default=EXPECTED_TARGET, help=f"目标指纹，固定为 {EXPECTED_TARGET}")
    parser.add_argument("--confirm-erase", default="", help=f"写入阶段必须为：{CONFIRM_PHRASE}")
    parser.add_argument("--wait-power", type=float, default=30.0, help="等待冷启动状态包秒数")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "captures" / "private")
    args = parser.parse_args(argv)

    if args.target != EXPECTED_TARGET:
        raise SystemExit(f"目标指纹只允许 {EXPECTED_TARGET}，收到 {args.target}")
    image = check_firmware(args.firmware)
    available = {port.device.upper(): port for port in enumerate_ports()}
    if args.port.upper() not in available:
        raise SystemExit(f"当前枚举中没有 {args.port}")
    recovery = check_recovery_tool()

    if args.phase == "write" and args.confirm_erase != CONFIRM_PHRASE:
        raise SystemExit(f"写入阶段必须显式给出 --confirm-erase \"{CONFIRM_PHRASE}\"")

    owner = PORT_LEASES.owner_for(args.port)
    if owner is not None:
        raise SystemExit(f"{args.port} 已由 {owner} 占用")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = args.log_dir / f"a2-{args.port.replace(':', '')}-{timestamp}.jsonl"
    header = {
        "port": args.port,
        "firmware": str(args.firmware.resolve()),
        "firmware_sha256": sha256(args.firmware),
        "target": args.target,
        "phase": args.phase,
        "recovery": recovery,
    }
    stream, emit = make_logger(log_path, header)

    print(f"A2 {args.phase}：{args.port}，固件 {args.firmware}（{image.data_size} 字节）")
    print(f"恢复检查：{recovery}")
    print(f"日志：{log_path}")
    print("打开串口可能改变 DTR/RTS；请在提示后手动断电再上电。")

    session = Stc89DownloadSession(
        image,
        PySerialTransport(args.port),
        emit,
        allow_erase=args.phase == "write",
        wait_power_seconds=args.wait_power,
    )
    lease = PORT_LEASES.acquire(args.port, owner="stc-isp-a2")
    try:
        state = session.run()
    finally:
        lease.release()
        stream.close()

    print("\n结果字段：")
    for line in describe(session, image):
        print(f"  {line}")
    print(f"终端状态：{state}; 错误：{session.error}")

    if args.phase == "precheck":
        if state == SessionState.FAILED and session.status is not None and session.baud_plan is not None:
            print("PRECHECK OK：身份/范围/参数已计算，擦写门禁按预期保持锁定；未发送协商或擦除命令。")
            return 0
        print("PRECHECK FAILED：未得到可用的识别与参数计划。")
        return 1

    if state == SessionState.COMPLETE:
        if not session.erase_started:
            print("异常：COMPLETE 但未进入擦除阶段，请勿当作烧录成功。")
            return 1
        print(f"WRITE COMPLETE：{session.acknowledged_bytes} 字节块 ACK 匹配；未做全片回读。")
        print("请手动断电再上电，确认程序实际运行；否则用官方工具恢复。")
        return 0
    print("WRITE FAILED：不自动重试。擦除一旦开始，程序可能不完整；请断电重启后用官方工具恢复。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
