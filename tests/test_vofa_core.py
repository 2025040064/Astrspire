"""Offline-only tests: no serial port or native VOFA+ process is touched."""

from __future__ import annotations

import csv
import json
import struct
from datetime import datetime, timezone

import pytest

from qx_stc_isp.vofa import (
    CsvRecorder,
    MonitorSession,
    TelemetryEvent,
    WorkspaceSettings,
    create_decoder,
    load_workspace,
    save_workspace,
)
from qx_stc_isp.vofa.decoders import JUSTFLOAT_TAIL, FireWaterDecoder, JustFloatDecoder


@pytest.mark.parametrize("name", ["FireWater", "JustFloat", "RawData"])
def test_decoder_factory_accepts_installed_formats(name):
    assert create_decoder(name) is not None


def test_decoder_factory_rejects_unknown_format():
    with pytest.raises(ValueError, match="未知数据格式"):
        create_decoder("unverified-plugin")


def test_firewater_handles_fragmented_lines_prefix_and_text():
    decoder = create_decoder("FireWater")
    assert decoder.feed(b"samples: 1.1, -2") == []
    events = decoder.feed(b".5\r\nhello\n3e-2,4\n")
    assert len(events) == 3
    assert events[0].samples == (1.1, -2.5)
    assert events[0].raw == b"samples: 1.1, -2.5\r\n"
    assert events[1].samples is None and events[1].text == "hello"
    assert events[2].samples == (0.03, 4.0)


def test_firewater_bounds_unterminated_line_and_recovers():
    decoder = FireWaterDecoder(max_frame_bytes=8)
    result = decoder.feed(b"0123456789\n2\n")
    assert result[0].error is not None
    assert result[-1].samples == (2.0,)


def test_justfloat_handles_fragmented_and_multiple_frames():
    decoder = create_decoder("JustFloat")
    first = struct.pack("<2f", 1.5, -2.0) + JUSTFLOAT_TAIL
    second = struct.pack("<f", 3.25) + JUSTFLOAT_TAIL
    assert decoder.feed(first[:7]) == []
    result = decoder.feed(first[7:] + second)
    assert [event.samples for event in result] == [(1.5, -2.0), (3.25,)]
    assert [event.raw for event in result] == [first, second]


def test_justfloat_rejects_misaligned_payload_and_recovers():
    decoder = JustFloatDecoder()
    result = decoder.feed(b"bad" + JUSTFLOAT_TAIL + struct.pack("<f", 2.0) + JUSTFLOAT_TAIL)
    assert result[0].error is not None
    assert result[1].samples == (2.0,)


def test_justfloat_bounds_noise_and_recovers_next_frame():
    decoder = JustFloatDecoder(max_frame_bytes=16)
    overflow = decoder.feed(b"x" * 40)
    assert len(overflow) == 1 and overflow[0].error is not None
    valid = decoder.feed(struct.pack("<f", 4.0) + JUSTFLOAT_TAIL)
    assert valid[0].samples == (4.0,)


def test_rawdata_preserves_input_without_sampling():
    decoder = create_decoder("RawData")
    assert decoder.feed(b"") == []
    event = decoder.feed(b"\x00\xff")[0]
    assert event.raw == b"\x00\xff"
    assert event.samples is None


def test_monitor_session_only_reads_and_closes():
    class Source:
        def __init__(self):
            self.calls = []

        def read_some(self, size, timeout):
            self.calls.append((size, timeout))
            return b"1,2\n"

        def close(self):
            self.calls.append("close")

        def write(self, _data):
            raise AssertionError("被动监视不得发送数据")

    source = Source()
    with MonitorSession(source, create_decoder("FireWater")) as monitor:
        assert monitor.read_once(size=64, timeout=0.2)[0].samples == (1.0, 2.0)
    assert source.calls == [(64, 0.2), "close"]


def test_workspace_roundtrip_does_not_connect_device(tmp_path):
    source = WorkspaceSettings(
        port="COM42", baudrate=230400, decoder="JustFloat", channel_names=("温度", "转速")
    )
    path = tmp_path / "monitor.json"
    save_workspace(path, source)
    assert load_workspace(path) == source
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 8


def test_workspace_keeps_baudrate_unset(tmp_path):
    path = tmp_path / "blank-baud.json"
    save_workspace(path, WorkspaceSettings(port="COM42"))
    assert load_workspace(path).baudrate is None


@pytest.mark.parametrize(
    "invalid",
    [
        {"schema_version": 2, "port": "", "baudrate": 115200, "decoder": "RawData", "channel_names": []},
        {"schema_version": 1, "port": "", "baudrate": 0, "decoder": "RawData", "channel_names": []},
        {"schema_version": 1, "port": "", "baudrate": 115200, "decoder": "unknown", "channel_names": []},
    ],
)
def test_workspace_rejects_invalid_settings(tmp_path, invalid):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError):
        load_workspace(path)


def test_csv_recorder_writes_long_format_and_never_overwrites(tmp_path):
    path = tmp_path / "samples.csv"
    event = TelemetryEvent(raw=b"1,2\n", samples=(1.0, 2.0))
    with CsvRecorder(path, channel_names=("电压",)) as recorder:
        assert recorder.record(event, at=datetime(2026, 10, 20, tzinfo=timezone.utc)) == 2
        assert recorder.record(TelemetryEvent(raw=b"text")) == 0
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["timestamp_utc", "channel_index", "channel_name", "value"]
    assert rows[1][1:] == ["0", "电压", "1.0"]
    assert rows[2][1:] == ["1", "CH2", "2.0"]
    with pytest.raises(FileExistsError):
        CsvRecorder(path).open()
