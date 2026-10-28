"""Live image cards share one bounded store; no device or file access."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.vofa import TelemetryEvent
from qx_stc_isp.vofa.controls import ControlSpec, ControlWorkspace
from qx_stc_isp.vofa.image_formats import DecodedNativeFrame
from qx_stc_isp.vofa.image_store import ImageFrameStore

def png_bytes(width: int = 2, height: int = 2) -> bytes:
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(0xFF0000FF)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


def fake_decoder(calls: list):
    def decode(payload: bytes, metadata: tuple[int, int, int]):
        calls.append((len(payload), metadata))
        image = QImage(2, 2, QImage.Format_RGB32)
        image.fill(0xFF00FF00)
        return DecodedNativeFrame(image, "PNG", ".png", True, frozenset({".png"}), metadata)
    return decode


def test_store_generations_payload_budget_and_pinning():
    calls: list = []
    store = ImageFrameStore(payload_limit=8, decoded_limit=1024 * 1024, decoder=fake_decoder(calls))
    assert store.put(1, b"aaaa") == 1
    assert store.put(1, b"bbbb") == 2
    assert store.get(1)[2] == 2
    assert store.put(2, b"cccc") == 1
    store.set_pinned({1})
    assert store.put(3, b"dddd") == 1  # 12 bytes over an 8-byte budget
    assert store.get(2) is None
    assert store.get(3) is not None
    assert store.get(1) is not None  # pinned survives while another candidate exists


def test_store_decodes_once_per_generation_and_caches_error(qtbot):
    calls: list = []
    store = ImageFrameStore(decoder=fake_decoder(calls))
    payload = png_bytes()
    store.put(4, payload, None)
    first, error, generation = store.pixmap(4)
    second, error2, generation2 = store.pixmap(4)
    assert error is None and error2 is None
    assert first is not None and first is second
    assert generation == generation2 == 1
    assert len(calls) == 1
    store.put(4, payload, None)
    store.pixmap(4)
    assert len(calls) == 2
    bad = ImageFrameStore(decoder=lambda _payload, _meta: (_ for _ in ()).throw(ValueError("bad")))
    bad.put(0, b"xx", (2, 2, 32))
    assert bad.pixmap(0)[0] is None
    assert "bad" in (bad.pixmap(0)[1] or "")
    assert bad.decoded_bytes == 0


@pytest.mark.parametrize("settings", [
    {"image_source": "live", "image_index": 0},
    {"image_source": "local", "image_path": "C:/pic.png"},
])
def test_image_spec_accepts_both_sources(settings):
    spec = ControlSpec("image", 0, "", "", settings)
    assert spec.settings["image_source"] == settings["image_source"]


@pytest.mark.parametrize("settings", [
    {"image_source": "stream"},
    {"image_source": "live", "image_index": 16},
    {"image_source": "live", "image_index": True},
])
def test_image_spec_rejects_invalid_source_or_index(settings):
    with pytest.raises(ValueError):
        ControlSpec("image", 0, "", "", settings)


def test_hidden_live_card_does_not_decode_and_visible_card_shows_frame(qtbot):
    calls: list = []
    store = ImageFrameStore(decoder=fake_decoder(calls))
    workspace = ControlWorkspace()
    qtbot.addWidget(workspace)
    workspace.set_image_store(store)
    card = workspace.add_control("image", 0, "", "", {"image_source": "live", "image_index": 1})
    store.put(1, png_bytes(), None)
    workspace.refresh_live_images()
    assert calls == []
    workspace.show()
    qtbot.waitUntil(card.isVisible, timeout=1000)
    workspace.refresh_live_images()
    assert len(calls) == 1
    assert card.image_label.pixmap() is not None
    assert "等待帧" not in card.image_label.text()


def test_main_window_feeds_live_card_from_cached_image(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("serial.Serial", lambda *_args, **_kwargs: pytest.fail("no serial"))
    window = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(window)
    window.show()
    window.visual_tabs.setCurrentIndex(1)
    card = window.control_workspace.add_control(
        "image", 0, "", "", {"image_source": "live", "image_index": 0}
    )
    window._refresh_control_images()
    window._handle_monitor_events([
        TelemetryEvent(raw=b"", image=png_bytes(), image_index=0, image_metadata=None)
    ])
    qtbot.waitUntil(lambda: card.image_label.pixmap() is not None, timeout=1500)
    assert card.image_label.pixmap() is not None


def test_image_directory_replay_updates_live_card(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("serial.Serial", lambda *_args, **_kwargs: pytest.fail("no serial"))
    folder = tmp_path / "frames"
    folder.mkdir()
    (folder / "01.png").write_bytes(png_bytes())
    (folder / "02.png").write_bytes(png_bytes(4, 4))
    window = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(window)
    window.show()
    window.visual_tabs.setCurrentIndex(1)
    card = window.control_workspace.add_control(
        "image", 0, "", "", {"image_source": "live", "image_index": 0}
    )
    window._refresh_control_images()
    window.source_combo.setCurrentIndex(window.source_combo.findData("images"))
    window.image_directory_edit.setText(str(folder))
    window._update_monitor_controls()
    window.start_monitor()
    qtbot.waitUntil(lambda: card.image_label.pixmap() is not None, timeout=3000)
    window.stop_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=3000)
