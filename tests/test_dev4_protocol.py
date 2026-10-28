"""Pure public protocol interfaces; no hardware, external scripts or fixtures."""
import pytest


def test_crc_named_variants_have_independent_standard_wire_vectors():
    from qx_stc_isp.vofa.protocol import checksum_bytes
    assert checksum_bytes("crc8-smbus", b"123456789") == bytes.fromhex("F4")
    assert checksum_bytes("crc16-modbus", b"123456789") == bytes.fromhex("37 4B")
    assert checksum_bytes("crc16-ccitt-false", b"123456789") == bytes.fromhex("29 B1")
    assert checksum_bytes("crc32-iso-hdlc", b"123456789") == bytes.fromhex("26 39 F4 CB")


def example_definition():
    return {"header_hex": "AA", "tail_hex": "55", "fields": [
        {"id": "size", "type": "length", "width": 1, "length_of": "data"},
        {"id": "data", "type": "bytes", "length_from": "size"},
        {"id": "value", "type": "uint16", "endian": "little"},
    ], "checksum": {"algorithm": "sum8", "start": 0, "end": "checksum"}}


def test_template_preview_and_stream_parser_share_explicit_byte_layout():
    from qx_stc_isp.vofa.protocol import FrameDefinition, FrameDecoder, FrameTemplate
    definition = FrameDefinition.from_document(example_definition())
    packet = FrameTemplate(definition).encode({"data": "10 20", "value": 0x1234})
    assert packet == bytes.fromhex("AA 02 10 20 34 12 22 55")
    decoder = FrameDecoder(definition)
    assert decoder.feed(b"noise" + packet[:3]) == []
    frames = decoder.feed(packet[3:] + packet)
    assert len(frames) == 2 and all(frame.checksum_valid for frame in frames)
    assert frames[0].values["data"] == bytes.fromhex("10 20")
    assert frames[0].values["value"] == 0x1234
    assert decoder.discarded_noise == 5 and decoder.pending_bytes == 0


def test_bad_checksum_is_diagnostic_and_next_frame_resynchronizes():
    from qx_stc_isp.vofa.protocol import FrameDefinition, FrameDecoder, FrameTemplate
    definition = FrameDefinition.from_document(example_definition())
    good = FrameTemplate(definition).encode({"data": "10 20", "value": 0x1234})
    bad = good[:-2] + b"\x00\x55"
    frames = FrameDecoder(definition).feed(bad + good)
    assert [frame.checksum_valid for frame in frames] == [False, True]
    assert "校验" in frames[0].error


def test_registered_frame_engine_feeds_only_valid_numeric_samples():
    from qx_stc_isp.vofa.plugins import EngineManifest, DeclarativeDecoder
    manifest = EngineManifest.from_document({
        "schema_version": 2, "id": "test.frame", "name": "帧测试", "mode": "framed",
        "options": {"definition": example_definition(), "channels": ["value"]},
    })
    decoder = DeclarativeDecoder(manifest)
    events = decoder.feed(bytes.fromhex("AA 02 10 20 34 12 00 55 AA 02 10 20 34 12 22 55"))
    assert events[0].error and events[0].samples is None
    assert events[1].samples == (4660.0,) and events[1].names == ("value",)


def test_declared_field_length_must_match_fixed_field_not_only_dynamic_bytes():
    from qx_stc_isp.vofa.protocol import FrameDefinition, FrameDecoder
    definition = FrameDefinition.from_document({
        "header_hex": "AA", "tail_hex": "55",
        "fields": [{"id": "size", "type": "length", "width": 1, "length_of": "value"},
                   {"id": "value", "type": "uint16"}],
    })
    frames = FrameDecoder(definition).feed(bytes.fromhex("AA 01 34 12 55 AA 02 34 12 55"))
    assert frames[0].error and not frames[0].checksum_valid
    assert frames[-1].checksum_valid and frames[-1].values["value"] == 4660


def test_bitfield_msb_and_signed_values_and_explicit_bad_layouts():
    from qx_stc_isp.vofa.protocol import FrameDefinition, FrameDecoder, FrameTemplate
    definition = FrameDefinition.from_document({"header_hex": "AA", "fields": [
        {"id": "flags", "type": "bits", "width": 1,
         "bits": [{"id": "signed", "offset": 0, "length": 3, "signed": True},
                  {"id": "low", "offset": 4, "length": 4}]}]})
    raw = FrameTemplate(definition).encode({"flags": 0xD2})
    assert raw == b"\xaa\xd2"
    frame = FrameDecoder(definition).feed(raw)[0]
    assert frame.values["flags.signed"] == -2 and frame.values["flags.low"] == 2
    bad = definition.document()
    bad["fields"][0]["bits"][0]["length"] = 9
    with pytest.raises(ValueError, match="fields"):
        FrameDefinition.from_document(bad)


@pytest.mark.parametrize("cut", range(1, 8))
def test_each_split_of_independent_literal_frame_is_lossless(cut):
    from qx_stc_isp.vofa.protocol import FrameDefinition, FrameDecoder
    decoder = FrameDecoder(FrameDefinition.from_document(example_definition()))
    raw = bytes.fromhex("AA 02 10 20 34 12 22 55")
    assert not decoder.feed(raw[:cut])
    result = decoder.feed(raw[cut:])
    assert len(result) == 1 and result[0].raw == raw
    assert result[0].values["value"] == 4660
