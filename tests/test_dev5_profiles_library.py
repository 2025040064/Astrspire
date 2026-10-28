"""Named profiles and frozen operations: encoding never opens a transport."""
import pytest


def profile_document(revision=1):
    return {"schema_version": 1, "id": "demo.sensor", "name": "演示传感器", "revision": revision,
        "connection": {"kinds": ["serial", "tcp_client"], "baudrate": 115200},
        "definition": {"header_hex": "AA", "fields": [{"id": "reply", "type": "uint8"}]},
        "channels": ["reply"], "operations": [{"id": "set", "name": "设置值", "definition": {
            "header_hex": "CC", "fields": [{"id": "value", "type": "uint16", "endian": "little"}],
            "checksum": {"algorithm": "sum8", "start": 0, "end": "checksum"}},
            "parameters": [{"id": "value", "type": "integer", "minimum": 0, "maximum": 1000, "default": 300}],
            "timeout_ms": 1000, "response": {"fields": {"reply": 7}}}]}


def test_frozen_operation_survives_library_revision_and_validates_parameters():
    from qx_stc_isp.vofa.device_profiles import DeviceProfile, ProfileLibrary
    library = ProfileLibrary()
    library.add(DeviceProfile.from_document(profile_document()))
    frozen = library.freeze_operation("demo.sensor", "set", 1, {})
    assert frozen.payload == bytes.fromhex("CC 2C 01 F9")
    next_doc = profile_document(2)
    next_doc["operations"][0]["parameters"][0]["default"] = 5
    library.add(DeviceProfile.from_document(next_doc))
    assert frozen.payload == bytes.fromhex("CC 2C 01 F9")
    assert library.encode_operation("demo.sensor", "set", 2, {}) == bytes.fromhex("CC 05 00 D1")
    assert ProfileLibrary.from_document(library.document()).document() == library.document()
    with pytest.raises(ValueError, match="value"):
        library.encode_operation("demo.sensor", "set", 1, {"value": 1001})
    with pytest.raises(ValueError, match="修订"):
        library.get("demo.sensor", 3)


def test_request_waits_for_confirmed_write_and_valid_response_without_retries():
    from qx_stc_isp.vofa.device_profiles import DeviceProfile, ProfileLibrary, RequestResponseTask
    from qx_stc_isp.vofa.protocol import DecodedFrameEvent, FrameDecoder
    library = ProfileLibrary()
    profile = DeviceProfile.from_document(profile_document())
    library.add(profile)
    task = RequestResponseTask(library.freeze_operation(profile.identifier, "set", 1, {}))
    assert task.start() == bytes.fromhex("CC 2C 01 F9")
    with pytest.raises(ValueError):
        task.start()
    valid = DecodedFrameEvent(profile.identifier, 1, FrameDecoder(profile.definition).feed(b"\xaa\x07")[0])
    assert not task.accept(valid)
    task.written(4, now=10.0)
    assert task.accept(valid) and task.state == "complete"
    partial = RequestResponseTask(task.operation)
    partial.start()
    partial.written(2, now=10.0)
    assert partial.state == "failed" and "部分" in partial.error
    timeout = RequestResponseTask(task.operation)
    timeout.start()
    timeout.written(4, now=10.0)
    timeout.poll(now=11.1)
    assert timeout.state == "failed" and "超时" in timeout.error


def test_profile_decoder_preserves_revision_for_response_and_returns_diagnostics():
    from qx_stc_isp.vofa.device_profiles import DeviceProfile, ProfileDecoder, ProfileLibrary
    profile = DeviceProfile.from_document(profile_document(2))
    library = ProfileLibrary()
    library.add(profile)
    decoder = ProfileDecoder(profile)
    event = decoder.feed(b"\xaa\x07")[0]
    assert event.frame.revision == 2 and event.frame.fields == {"reply": 7}
    assert event.samples == (7.0,)
    assert library.freeze_operation(profile.identifier, "set", 2, {}).matcher.matches(event.frame)
    decoder.reset()
    assert decoder.feed(b"\xaa") == []


def test_library_immutable_revision_bad_response_and_cancel_do_not_match():
    from qx_stc_isp.vofa.device_profiles import DeviceProfile, ProfileDecoder, ProfileLibrary, RequestResponseTask
    library = ProfileLibrary()
    original = DeviceProfile.from_document(profile_document())
    library.add(original)
    changed = profile_document()
    changed["name"] = "不能覆盖"
    with pytest.raises(ValueError, match="覆盖"):
        library.add(DeviceProfile.from_document(changed))
    task = RequestResponseTask(library.freeze_operation(original.identifier, "set", 1, {}))
    task.start()
    task.written(4)
    assert not task.accept(ProfileDecoder(original).feed(b"\xaa\x08")[0].frame)
    task.cancel()
    assert not task.accept(ProfileDecoder(original).feed(b"\xaa\x07")[0].frame)
    assert task.state == "cancelled"
