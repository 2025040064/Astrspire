"""Explicit UART fixture acceptance. Importing this file never opens hardware."""
import argparse
import csv
from dataclasses import asdict
import json
import math
from pathlib import Path
import secrets
import threading
import time

from PySide6.QtCore import Qt

from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.transport import PySerialTransport
from qx_stc_isp.vofa.recorder import AsyncRecorder, CsvRecorder
from qx_stc_isp.vofa.serial_link import SerialLinkWorker

HELLO = b"ASTRSPIRE:G431_UART_V1,115200,16,10\r\n"
START = b"ASTR:START\n"
STOP = b"ASTR:STOP\n"


def make_fixture_transport(device):
    return PySerialTransport(device, dtr=False, rts=False)


class FrameVerifier:
    def __init__(self):
        self.frames = self.gaps = self.duplicates = self.out_of_order = 0
        self.value_errors = self.tick_errors = self.uart_errors = 0
        self.last_sequence = self.last_tick = None

    def observe(self, values):
        self.frames += 1
        if len(values) != 16 or not all(math.isfinite(value) for value in values):
            self.value_errors += 1
            return
        sequence, tick = values[:2]
        if (sequence != int(sequence) or not 1 <= sequence < 2**24
                or tick != int(tick) or not 0 <= tick < 2**24):
            self.value_errors += 1
            return
        if self.last_sequence is not None:
            delta = sequence - self.last_sequence
            self.duplicates += delta == 0
            self.out_of_order += delta < 0
            self.gaps += max(0, int(delta) - 1)
            self.tick_errors += tick - self.last_tick < 10
        for channel in range(2, 15):
            expected = (int(sequence) % 200 - 100) * (channel - 1) / 100
            if not math.isclose(values[channel], expected, abs_tol=2e-6):
                self.value_errors += 1
        self.uart_errors += values[15] != 0
        self.last_sequence, self.last_tick = sequence, tick

    def report(self):
        return {name: value for name, value in vars(self).items() if not name.startswith("last_")}

    @property
    def passed(self):
        return self.frames > 0 and not any(value for key, value in self.report().items() if key != "frames")


def handshake(transport):
    """Only the explicit hardware runner calls this; no erase or reset commands."""
    def read_line():
        pending = bytearray()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(pending) < 128:
            pending.extend(transport.read_some(128 - len(pending), 0.05))
            if pending.endswith(b"\n"):
                return bytes(pending)
        if len(pending) >= 128:
            raise ValueError(f"Fixture handshake oversized reply: received {len(pending)} bytes without LF")
        raise ValueError(f"Fixture handshake timeout: received {len(pending)} bytes without a complete LF reply")

    transport.open(115200)
    try:
        transport.purge_buffers()
        transport.write(b"ASTR:HELLO\n")
        if read_line() != HELLO:
            raise ValueError("This port did not identify as the installed G431 UART test fixture")
        token = secrets.token_hex(6).encode("ascii")
        transport.write(b"ASTR:PING:" + token + b"\n")
        if read_line() != b"PONG:" + token + b"\r\n":
            raise ValueError("Bidirectional nonce reply did not match")
    finally:
        transport.close()


def check_csv(path):
    verifier = FrameVerifier()
    values = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if int(row["channel_index"]) != len(values):
                raise ValueError("CSV channels are missing or out of order")
            values.append(float(row["value"]))
            if len(values) == 16:
                verifier.observe(values)
                values = []
    if values:
        raise ValueError("CSV ended with an incomplete frame")
    return verifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--vid", type=lambda value: int(value, 16), required=True)
    parser.add_argument("--pid", type=lambda value: int(value, 16), required=True)
    parser.add_argument("--seconds", type=int, choices=(60, 600), default=60)
    parser.add_argument("--fixture-installed", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.fixture_installed:
        parser.error("Requires an explicitly installed, backed-up G431 test fixture; never flashes automatically")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if root.drive.upper() != "F:" or not output.is_relative_to(root / "build-preview"):
        parser.error("Keep hardware evidence in this F-drive project's build-preview folder")
    port = next((item for item in enumerate_ports() if item.device.casefold() == args.port.casefold()), None)
    if port is None or (port.vid, port.pid) != (args.vid, args.pid):
        parser.error("Serial identity mismatch; no device opened")
    output.mkdir(parents=True, exist_ok=False)
    state = dict(scope="Hardware UART backend and AsyncRecorder; not GUI or SWD acceptance", device=asdict(port),
        baudrate=115200, seconds=args.seconds, handshake=False, passed=False, errors=[])
    verifier = FrameVerifier()
    recorder = AsyncRecorder(CsvRecorder(output / "samples.csv"))
    worker = SerialLinkWorker(port.device, 115200, "JustFloat")
    started = None
    capture_timer = None
    stopping = threading.Event()
    transmitted = []

    def stop_stream():
        if stopping.is_set():
            return
        stopping.set()
        try:
            worker.enqueue_send(STOP)
        except Exception as exc:
            state["errors"].append(str(exc))
            worker.stop()

    def sent(payload):
        transmitted.append(payload.decode("ascii").rstrip())
        if payload == STOP:
            worker.stop()

    def receive(events):
        nonlocal started, capture_timer
        for event in events:
            if event.error:
                state["errors"].append(event.error)
                stop_stream()
            elif event.samples is not None:
                if started is None:
                    started = time.monotonic()
                    capture_timer = threading.Timer(args.seconds, stop_stream)
                    capture_timer.start()
                verifier.observe(event.samples)
                if not recorder.record(event) or not verifier.passed:
                    state["errors"].append("Sample or recording validation failed")
                    stop_stream()

    def deadline():
        state["errors"].append("UART acceptance deadline expired")
        worker.stop()

    watchdog = threading.Timer(args.seconds + 10, deadline)
    try:
        with PORT_LEASES.acquire(port.device, owner="uart-fixture-handshake"):
            handshake(make_fixture_transport(port.device))
        state["handshake"] = True
        recorder.open()
        worker.status.connect(lambda kind, text: worker.enqueue_send(START) if kind == "connected" else None, Qt.DirectConnection)
        worker.transmitted.connect(sent, Qt.DirectConnection)
        worker.events.connect(receive, Qt.DirectConnection)
        worker.error.connect(state["errors"].append, Qt.DirectConnection)
        watchdog.start()
        worker.run()
    except Exception as exc:
        state["errors"].append(str(exc))
    finally:
        for timer in (capture_timer, watchdog):
            if timer is not None:
                timer.cancel()
                if timer.ident is not None:
                    timer.join(timeout=1)
        state["recorder_closed"] = recorder.close(timeout=5)
        state["elapsed_seconds"] = time.monotonic() - started if started is not None else 0
        state.update(samples=verifier.report(), recording=asdict(recorder.statistics),
                     receive=asdict(worker.receive_statistics), transmit=asdict(worker.statistics), commands=transmitted,
                     port_released=PORT_LEASES.owner_for(port.device) is None)
        if (output / "samples.csv").exists() and state["recorder_closed"]:
            try:
                csv_verifier = check_csv(output / "samples.csv")
                state["csv"] = csv_verifier.report()
                state["csv_matches"] = csv_verifier.report() == verifier.report()
            except Exception as exc:
                state["errors"].append(str(exc))
        state["passed"] = bool(state["handshake"] and verifier.passed and state.get("csv_matches")
            and not state["errors"] and state["recorder_closed"] and state["port_released"]
            and state["recording"]["saved"] == verifier.frames and state["recording"]["unsaved"] == 0
            and state["recording"]["error"] is None and transmitted[-1:] == ["ASTR:STOP"]
            and args.seconds * 95 <= verifier.frames <= args.seconds * 105)
        (output / "result.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0 if state["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
