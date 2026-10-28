"""Protocol public seam: preserve exact fields before waveform conversion."""
import json

from qx_stc_isp.vofa.plugins import DeclarativeDecoder, EngineManifest
from qx_stc_isp.vofa.protocol import FrameDefinition, FrameDecoder, FrameTemplate


def test_inexact_integer_channel_reports_diagnostic_and_keeps_original_value():
    manifest = EngineManifest.from_document({
        "schema_version": 2, "id": "test.precision", "name": "精度", "mode": "framed",
        "options": {"channels": ["flags.value"], "definition": {
            "header_hex": "AA", "fields": [{"id": "flags", "type": "bits", "width": 8,
                "bits": [{"id": "value", "offset": 0, "length": 64}]}]}}})
    decoder = DeclarativeDecoder(manifest)
    event = decoder.feed(bytes.fromhex("AA 00 20 00 00 00 00 00 01"))[0]
    assert event.samples is None
    assert "精度" in event.error
    assert json.loads(event.text)["flags.value"] == 9007199254740993


def array_definition():
    return {"header_hex": "AA", "tail_hex": "55", "fields": [
        {"id": "message", "type": "id", "width": 1, "enum": {"1": "采样", "2": "状态"}},
        {"id": "values", "type": "array", "item_type": "int16", "count": 2,
         "endian": "little", "when": {"id": "message", "equals": 1}},
        {"id": "status", "type": "uint8", "when": {"id": "message", "equals": 2}}],
        "checksum": {"algorithm": "sum8", "start": 0, "end": "checksum"}}


def test_array_enum_and_message_branch_have_exact_byte_spans():
    definition = FrameDefinition.from_document(array_definition())
    raw = FrameTemplate(definition).encode({"message": 1, "values": [-2, 300]})
    assert raw == bytes.fromhex("AA 01 FE FF 2C 01 D5 55")
    decoder = FrameDecoder(definition)
    assert decoder.feed(raw[:5]) == []
    frame = decoder.feed(raw[5:])[0]
    assert frame.values["values"] == (-2, 300)
    assert frame.values["values[1]"] == 300
    assert frame.spans["values[1]"] == (4, 6)
    assert frame.spans["message"] == (1, 2)
    assert frame.labels["message"] == "采样"
    assert frame.field_types["values[1]"] == "int16"
    assert frame.checksum_span == (6, 7) and frame.checksum_coverage == (0, 6)
    assert "status" not in frame.values


def test_decoder_exposes_structured_event_once_without_reparsing():
    manifest = EngineManifest.from_document({"schema_version": 2, "id": "test.typed", "name": "字段",
        "mode": "framed", "options": {"definition": array_definition(), "channels": ["values[1]"]}})
    decoder = DeclarativeDecoder(manifest)
    assert decoder.feed(bytes.fromhex("AA 01 FE FF 2C 01 D5 55"))[0].samples == (300.0,)
    frames = decoder.take_decoded_frames()
    assert len(frames) == 1
    assert frames[0].protocol_id == "test.typed"
    assert frames[0].frame.values["values[0]"] == -2
    assert decoder.take_decoded_frames() == ()


def test_dynamic_array_uses_explicit_count_semantics_not_guessed_byte_length():
    definition = FrameDefinition.from_document({"header_hex": "AA", "fields": [
        {"id": "count", "type": "length", "length_mode": "count", "length_of": "data"},
        {"id": "data", "type": "array", "item_type": "int16", "count_from": "count"}]})
    packet = FrameTemplate(definition).encode({"data": [-2, 300]})
    assert packet == bytes.fromhex("AA 02 FE FF 2C 01")
    frame = FrameDecoder(definition).feed(packet)[0]
    assert frame.values["count"] == 2 and frame.values["data"] == (-2, 300)


def test_count_mode_cannot_reference_non_array_and_bad_counts_are_rejected():
    import pytest
    with pytest.raises(ValueError, match="count"):
        FrameDefinition.from_document({"header_hex": "AA", "fields": [
            {"id": "count", "type": "length", "length_mode": "count", "length_of": "data"},
            {"id": "data", "type": "bytes", "width": 2}]})
