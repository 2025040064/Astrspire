"""Synthetic parser cases, NOT official captures and NOT RUN this session."""

import struct

import pytest

from qx_stc_isp.vofa.image_protocol import FireWaterImageDecoder, JustFloatImageDecoder, JUSTFLOAT_TAIL
from qx_stc_isp.vofa.plugins import DeclarativeDecoder, EngineManifest, PluginRegistry


def test_ascii_native_envelope_respects_length_not_embedded_newline():
    # Deliberately not a decodable PNG: this exercises framing only, not QImage.
    payload = b"\x89PNG\r\n\x1a\nsynthetic"
    header = f"image:3,{len(payload)},16,12,28\n".encode("ascii")
    decoder = FireWaterImageDecoder()
    events = []
    for byte in header + payload + b"1,2\n":
        events.extend(decoder.feed(bytes([byte])))
    image = next(event for event in events if event.image is not None)
    assert image.image_index == 3 and image.image_metadata == (16, 12, 28)
    assert image.image == payload and image.raw == header + payload
    assert events[-1].samples == (1.0, 2.0)


def test_binary_native_envelope_is_little_endian_and_fragmented():
    payload = b"not-a-real-image"
    header = struct.pack("<7I", 2, len(payload), 4, 3, 27, 0x7F800000, 0x7F800000)
    decoder = JustFloatImageDecoder()
    events = []
    for byte in header + payload:
        events.extend(decoder.feed(bytes([byte])))
    assert len(events) == 1
    assert events[0].image_index == 2 and events[0].image == payload
    assert events[0].raw == header + payload


def test_unknown_bounded_image_is_skipped_before_numeric_resync():
    decoder = FireWaterImageDecoder()
    events = decoder.feed(b"image:0,4,1,1,99\nnoise1,2\n")
    assert any(event.error for event in events)
    assert all(event.image is None for event in events)
    # Four skipped bytes are 'nois'; the remaining 'e1,2' is text, not numbers.
    assert events[-1].samples is None


def test_justfloat_numeric_lookahead_is_explicit():
    decoder = JustFloatImageDecoder()
    first = struct.pack("<f", 1) + JUSTFLOAT_TAIL
    assert decoder.feed(first) == []
    events = decoder.feed(struct.pack("<f", 2))
    assert len(events) == 1 and events[0].samples == (1.0,)


def test_declarative_binary_engine_has_payload_only_checksum():
    manifest = EngineManifest.from_document({"schema_version": 1, "id": "fixture.binary", "name": "Synthetic", "mode": "binary_fixed",
        "options": {"types": ["uint16", "int16"], "endian": "little", "header_hex": "AA 55", "trailer_hex": "0D 0A", "checksum": "sum8"}})
    payload = struct.pack("<Hh", 123, -40)
    frame = b"\xaa\x55" + payload + bytes([sum(payload) & 255]) + b"\r\n"
    decoder = DeclarativeDecoder(manifest)
    assert decoder.feed(frame[:4]) == []
    events = decoder.feed(frame[4:])
    assert len(events) == 1 and events[0].samples == (123, -40)
    registry = PluginRegistry()
    registry.register(manifest)
    with pytest.raises(ValueError):
        registry.register(manifest)
