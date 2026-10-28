"""Bounded STC89 read-only check; never erases, programs, or resets a target."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.stc89_session import Stc89ReadOnlySession
from qx_stc_isp.transport import PySerialTransport


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.seconds <= 30:
        parser.error('seconds must be >0 and <=30')
    ports = enumerate_ports()
    port = next((p for p in ports if p.device.casefold() == args.port.casefold()), None)
    if port is None or (port.vid, port.pid) != (0x1A86, 0x7523):
        parser.error('selected port is not the expected CH340 1A86:7523; no port opened')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        def record(event):
            value = {'time': event.timestamp.isoformat(), 'kind': event.kind,
                     'state': event.state.name, 'message': event.message,
                     'direction': event.direction, 'hex': event.data.hex()}
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')
            stream.flush()

        stream.write(json.dumps({'started': datetime.now().astimezone().isoformat(),
                                 'device': asdict(port), 'purpose': '7F read-only identification; no erase/write/reset'},
                                ensure_ascii=False) + '\n')
        session = Stc89ReadOnlySession(PySerialTransport(port.device), record,
                                       wait_power_seconds=args.seconds)
        with PORT_LEASES.acquire(port.device, owner='bounded-readonly-check'):
            state = session.run()
        result = {'state': state.name, 'known_profile': session.known_profile,
                  'error': session.error, 'chip_id': session.status.chip_id if session.status else None,
                  'version': session.status.version_label if session.status else None,
                  'evidence': str(args.output.resolve())}
        stream.write(json.dumps({'result': result}, ensure_ascii=False) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if session.known_profile else 1


if __name__ == '__main__':
    raise SystemExit(main())
