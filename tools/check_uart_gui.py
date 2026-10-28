"""Explicit receive/stop GUI check using the application's real Qt event loop."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import qx_stc_isp.gui as gui
from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.transport import PySerialTransport

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="03", choices=("03", "04", "05"))
    args = parser.parse_args()
    ports = [p for p in enumerate_ports() if (p.vid, p.pid) == (0x0D28, 0x0204)]
    if len(ports) != 1 or ports[0].device != "COM7":
        raise ValueError("Expected the uniquely attached DAP on COM7")
    output = Path(__file__).resolve().parents[1] / f"build-preview/runtime-g431-uart-20261027/assistant-gui-{args.run}"
    output.mkdir(parents=True, exist_ok=False)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("TX forbidden in this receive-only GUI check")
    PySerialTransport.write = forbidden
    app = QApplication([])
    gui._load_bundled_fonts()
    app.setFont(QFont("PingFang SC", 10))
    window = gui.MainWindow(log_dir=output / "logs")
    window.tabs.setCurrentIndex(1)
    window.show()
    state = dict(port="COM7", connected=False, errors=[],
                 scope="Live GUI RX/stop only; no application TX, flashing, or reset")
    phase = ["connecting", time.monotonic() + 3]
    poll = QTimer()
    poll.setInterval(20)
    def finish():
        poll.stop()
        state["thread_stopped"] = not window._monitor_active and window._monitor_thread is None
        state["port_released"] = PORT_LEASES.owner_for("COM7") is None
        state["close_accepted"] = window.close()
        state["passed_idle_gui"] = bool(state["connected"] and not state["errors"]
            and state["thread_stopped"] and state["port_released"] and state["close_accepted"])
        (output / "result.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if state["close_accepted"]:
            app.quit()
    def stop():
        state["rx_bytes"], state["tx_bytes"] = window._rx_bytes, window._tx_bytes
        state["status"] = window.monitor_status_label.text()
        if window._monitor_worker is not None:
            state["receive"] = asdict(window._monitor_worker.receive_statistics)
            state["transmit"] = asdict(window._monitor_worker.statistics)
        state["image_saved"] = window.grab().save(str(output / "receiving.png"))
        if window._monitor_error:
            state["errors"].append(window._monitor_error)
        if state["tx_bytes"] or state.get("transmit", {}).get("accepted_bytes", 0):
            state["errors"].append("Unexpected application TX")
        window.stop_monitor()
        phase[:] = ["stopping", time.monotonic() + 5]
    def update():
        if phase[0] == "connecting":
            if window._monitor_connected:
                state["connected"] = True
                phase[:] = ["receiving", time.monotonic() + 4]
            elif window._monitor_error or time.monotonic() > phase[1]:
                state["errors"].append(window._monitor_error or "Connection deadline expired")
                stop()
        elif phase[0] == "receiving" and time.monotonic() > phase[1]:
            stop()
        elif phase[0] == "stopping":
            if not window._monitor_active:
                finish()
            elif time.monotonic() > phase[1]:
                state["errors"].append("Thread stop deadline expired")
                finish()
    window.serial_assistant_button.click()
    window.monitor_port_combo.setCurrentIndex(window.monitor_port_combo.findData("COM7"))
    window.monitor_risk.setChecked(True)
    window.start_monitor()
    poll.timeout.connect(update)
    poll.start()
    app.exec()
    print(json.dumps(state, ensure_ascii=True, indent=2))
    return 0 if state.get("passed_idle_gui") else 1

if __name__ == "__main__":
    raise SystemExit(main())
