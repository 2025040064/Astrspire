"""Behavioral examples for the explicitly approved SerialWeb adaptation."""

import pytest

from qx_stc_isp.vofa.transmit import encode_send_payload, TransmitError


def test_manual_utf16_send_uses_encoded_line_ending():
    assert encode_send_payload("中", encoding="utf-16le", line_ending="LF") == b"\x2d\x4e\x0a\x00"


def test_ascii_send_rejects_lossy_payload_in_desktop_adapter():
    with pytest.raises(TransmitError):
        encode_send_payload("中文", encoding="ascii")


def test_firewater_uses_serialweb_cr_lf_boundaries_across_reads():
    from qx_stc_isp.vofa.decoders import FireWaterDecoder
    decoder = FireWaterDecoder()
    assert [event.samples for event in decoder.feed(b"1,2\r")] == [(1., 2.)]
    assert [event.samples for event in decoder.feed(b"\n3,4\n5,6\r\n")] == [(3., 4.), (5., 6.)]


def test_serialweb_keeps_same_read_crlf_raw_bytes():
    from qx_stc_isp.vofa.decoders import FireWaterDecoder
    assert FireWaterDecoder().feed(b"1,2\r\n")[0].raw == b"1,2\r\n"


def test_serialweb_decoding_keeps_split_utf16_surrogate_pair():
    from qx_stc_isp.vofa.serialweb import SerialWebTextDecoder
    decoder = SerialWebTextDecoder("utf-16le")
    assert decoder.decode(b"\x3d\xd8") == ""
    assert decoder.decode(b"\x00\xde") == "\U0001f600"
