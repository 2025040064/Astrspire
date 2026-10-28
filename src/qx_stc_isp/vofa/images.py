"""Self-owned, bounded image display and explicit local JPEG/PNG playback.

The stream decoder recognises standard concatenated image files.  It is not a
decoder for an inferred VOFA+ private envelope.  Importing or constructing these
objects never opens a data source or starts a worker.
"""

from __future__ import annotations

import binascii
import math
import os
import re
import stat
import threading
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage, QImageWriter, QPainter, QPen, QTransform
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget

from ..appearance import theme_palette
from .image_formats import DecodedNativeFrame, ImageFormatError, decode_native_frame
from .image_effects import IMAGE_EFFECTS, IMAGE_EFFECT_LABELS, apply_image_effect, effect_parameter


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_FEED_BYTES = 256 * 1024
MAX_DIRECTORY_IMAGES = 10_000
MAX_DIRECTORY_ENTRIES = 50_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8"
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})


class ImageError(ValueError):
    """An explicitly selected image, directory or stream is invalid."""


class IncrementalImageDecoder:
    """Extract complete standard JPEG/PNG byte images from bounded chunks.

    JPEG marker lengths are respected, so an EOI-like byte pair inside an APP
    segment is not mistaken for the image end.  PNG chunk lengths and CRCs are
    checked before a frame is returned.  No pixels are decoded here.  Bad
    candidates are skipped and the next signature is sought.  ``errors`` holds
    only the most recent feed's diagnostics, rather than an unbounded history.
    """

    def __init__(self, max_frame_bytes: int = MAX_IMAGE_BYTES) -> None:
        if type(max_frame_bytes) is not int or not 1024 <= max_frame_bytes <= MAX_IMAGE_BYTES:
            raise ValueError("图像帧上限必须在 1024 字节至 8 MiB 之间")
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._kind = ""
        self._scan = 0
        self._jpeg_entropy = False
        self._png_first = True
        self.errors: tuple[str, ...] = ()
        self.discarded_bytes = 0

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()
        self._reset_candidate()
        self.errors = ()
        self.discarded_bytes = 0

    def _reset_candidate(self) -> None:
        self._kind = ""
        self._scan = 0
        self._jpeg_entropy = False
        self._png_first = True

    def _drop(self, count: int) -> None:
        if count:
            del self._buffer[:count]
            self.discarded_bytes += count
        self._reset_candidate()

    def _seek(self) -> bool:
        jpeg = self._buffer.find(JPEG_SIGNATURE)
        png = self._buffer.find(PNG_SIGNATURE)
        positions = [item for item in (jpeg, png) if item >= 0]
        if not positions:
            # Preserve an incomplete signature crossing the next chunk boundary.
            keep = 0
            for signature in (PNG_SIGNATURE, JPEG_SIGNATURE):
                for size in range(1, len(signature)):
                    if self._buffer.endswith(signature[:size]):
                        keep = max(keep, size)
            self._drop(len(self._buffer) - keep)
            return False
        offset = min(positions)
        if offset:
            self._drop(offset)
        if self._buffer.startswith(PNG_SIGNATURE):
            self._kind, self._scan = "png", len(PNG_SIGNATURE)
        else:
            self._kind, self._scan = "jpeg", len(JPEG_SIGNATURE)
        return True

    def _png_end(self) -> int | None:
        while True:
            if len(self._buffer) - self._scan < 8:
                return None
            length = int.from_bytes(self._buffer[self._scan:self._scan + 4], "big")
            kind = bytes(self._buffer[self._scan + 4:self._scan + 8])
            end = self._scan + 12 + length
            if end > self.max_frame_bytes:
                raise ImageError("PNG 块长度超出图像帧上限")
            if any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in kind):
                raise ImageError("PNG 块名称无效")
            if self._png_first and (kind != b"IHDR" or length != 13):
                raise ImageError("PNG 首块不是 13 字节 IHDR")
            if not self._png_first and kind == b"IHDR":
                raise ImageError("PNG 出现重复 IHDR")
            if kind == b"IEND" and length != 0:
                raise ImageError("PNG IEND 长度无效")
            if len(self._buffer) < end:
                return None
            payload_end = end - 4
            expected = int.from_bytes(self._buffer[payload_end:end], "big")
            actual = binascii.crc32(self._buffer[self._scan + 4:payload_end]) & 0xFFFFFFFF
            if expected != actual:
                raise ImageError("PNG 块 CRC 不匹配")
            if self._png_first:
                width = int.from_bytes(self._buffer[self._scan + 8:self._scan + 12], "big")
                height = int.from_bytes(self._buffer[self._scan + 12:self._scan + 16], "big")
                if not width or not height or width * height > MAX_IMAGE_PIXELS:
                    raise ImageError("PNG 像素尺寸无效或超过 1600 万像素")
                self._png_first = False
            self._scan = end
            if kind == b"IEND":
                return end

    def _jpeg_end(self) -> int | None:
        while True:
            if self._jpeg_entropy:
                marker_at = self._buffer.find(b"\xff", self._scan)
                if marker_at < 0:
                    self._scan = len(self._buffer)
                    return None
                cursor = marker_at + 1
                while cursor < len(self._buffer) and self._buffer[cursor] == 0xFF:
                    cursor += 1
                if cursor >= len(self._buffer):
                    self._scan = marker_at
                    return None
                marker = self._buffer[cursor]
                if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                    self._scan = cursor + 1
                    continue
                self._scan = marker_at
                self._jpeg_entropy = False
            if self._scan >= len(self._buffer):
                return None
            if self._buffer[self._scan] != 0xFF:
                raise ImageError("JPEG 标记前缀无效")
            cursor = self._scan + 1
            while cursor < len(self._buffer) and self._buffer[cursor] == 0xFF:
                cursor += 1
            if cursor >= len(self._buffer):
                return None
            marker = self._buffer[cursor]
            if marker == 0xD9:
                return cursor + 1
            if marker in (0x00, 0xD8):
                raise ImageError("JPEG 出现无效或嵌套 SOI 标记")
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                self._scan = cursor + 1
                continue
            if len(self._buffer) < cursor + 3:
                return None
            length = int.from_bytes(self._buffer[cursor + 1:cursor + 3], "big")
            if length < 2:
                raise ImageError("JPEG 段长度不足 2 字节")
            end = cursor + 1 + length
            if end > self.max_frame_bytes:
                raise ImageError("JPEG 段长度超出图像帧上限")
            if len(self._buffer) < end:
                return None
            self._scan = end
            self._jpeg_entropy = marker == 0xDA

    def feed(self, chunk: bytes) -> list[bytes]:
        if not isinstance(chunk, bytes):
            raise TypeError("图像输入必须为 bytes")
        if len(chunk) > MAX_FEED_BYTES:
            raise ImageError("单批图像输入不能超过 256 KiB，请分片输入")
        self._buffer.extend(chunk)
        frames: list[bytes] = []
        errors: list[str] = []
        bad_candidates = 0
        while self._buffer:
            if not self._kind and not self._seek():
                break
            try:
                end = self._png_end() if self._kind == "png" else self._jpeg_end()
                if end is None:
                    if len(self._buffer) > self.max_frame_bytes:
                        raise ImageError("图像帧超过上限，已跳过并重新同步")
                    break
                if end > self.max_frame_bytes:
                    raise ImageError("图像帧超过上限")
                frames.append(bytes(self._buffer[:end]))
                del self._buffer[:end]
                self._reset_candidate()
            except ImageError as exc:
                if len(errors) < 16:
                    errors.append(str(exc))
                self._drop(1)
                bad_candidates += 1
                if bad_candidates >= 64:
                    # Limit work on a batch consisting of repeated false SOIs.
                    # Retain only possible partial signatures, not a huge queue
                    # of invalid candidates that could freeze the GUI thread.
                    tail = bytes(self._buffer[-7:])
                    self._drop(len(self._buffer) - len(tail))
                    self._seek()
                    message = "本批损坏图像候选过多，已丢弃剩余候选并重新同步"
                    errors = (errors[:15] + [message])
                    break
        self.errors = tuple(errors)
        return frames


def decode_image(encoded: bytes) -> QImage:
    """Keep the original standalone JPEG/PNG API and actual-pixel budget."""
    if not isinstance(encoded, bytes) or not 1 <= len(encoded) <= MAX_IMAGE_BYTES:
        raise ImageError("图像数据必须非空且不超过 8 MiB")
    if not (encoded.startswith(PNG_SIGNATURE) or encoded.startswith(JPEG_SIGNATURE)):
        raise ImageError("只接受标准 JPEG 或 PNG 图像")
    try:
        format_id = 28 if encoded.startswith(PNG_SIGNATURE) else 27
        return decode_native_frame(encoded, (1, 1, format_id)).image
    except ImageFormatError as exc:
        raise ImageError(str(exc)) from exc


class _ImageCanvas(QWidget):
    changed = Signal()
    rotate_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = QImage()
        self.scale: float | None = None
        self.pan = QPointF()
        self._drag_at: QPointF | None = None
        self._palette = theme_palette("light")
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("图像显示区域")
        self.setAccessibleDescription("滚轮缩放，拖动平移；0自适应，1原始比例，R旋转")

    def display_scale(self) -> float:
        if self.image.isNull():
            return 1.0
        return self.scale if self.scale is not None else min(
            max(self.width() - 16, 1) / self.image.width(),
            max(self.height() - 16, 1) / self.image.height(),
        )

    def set_theme(self, theme: str) -> None:
        self._palette = theme_palette(theme)
        self.update()

    def zoom(self, factor: float) -> None:
        self.scale = max(0.05, min(32.0, self.display_scale() * factor))
        self.update()
        self.changed.emit()

    def fit(self) -> None:
        self.scale = None
        self.pan = QPointF()
        self.update()
        self.changed.emit()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._palette.background))
        if self.image.isNull():
            painter.setPen(QColor(self._palette.muted))
            painter.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignCenter | Qt.TextWordWrap, "等待图像")
            return
        scale = self.display_scale()
        width, height = self.image.width() * scale, self.image.height() * scale
        target = QRectF((self.width() - width) / 2 + self.pan.x(),
                        (self.height() - height) / 2 + self.pan.y(), width, height)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, scale < 1.0)
        painter.drawImage(target, self.image)
        painter.setPen(QPen(QColor(self._palette.border), 1))
        painter.drawRect(target)

    def wheelEvent(self, event) -> None:
        if self.image.isNull():
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.2 if delta > 0 else 1 / 1.2)
            event.accept()
        else:
            event.ignore()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self.image.isNull():
            self._drag_at = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_at is not None:
            current = event.position()
            self.pan += current - self._drag_at
            self._drag_at = current
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_at = None
            self.unsetCursor()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def focusOutEvent(self, event) -> None:
        self._drag_at = None
        self.unsetCursor()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom(1.2)
        elif event.key() == Qt.Key_Minus:
            self.zoom(1 / 1.2)
        elif event.key() == Qt.Key_0:
            self.fit()
        elif event.key() == Qt.Key_1:
            self.scale = 1.0
            self.pan = QPointF()
            self.update()
            self.changed.emit()
        elif event.key() == Qt.Key_R:
            self.rotate_requested.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class ImageViewWidget(QWidget):
    """Aspect-preserving image viewport; file writes require ``save_image``.

    Encoded input is saved as its original file; display effects do not silently
    recompress that source.  Raw native pixels can instead be saved as the
    current displayed PNG, or explicitly exported with ``save_raw_payload``.
    """

    error = Signal(str)
    status = Signal(str)
    image_changed = Signal(int, int)
    save_requested = Signal()
    configuration_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._original = QImage()
        self._encoded = b""
        self._source_format = ""
        self._default_suffix = ".png"
        self._allowed_suffixes: frozenset[str] = frozenset()
        self._frame_encoded = True
        self._native_metadata: tuple[int, int, int] | None = None
        self._rotation = 0
        self._effect = "none"
        self._effect_parameter = 0
        self._effect_preview_note = ""
        self._theme = "light"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        toolbar = QGridLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setHorizontalSpacing(4)
        toolbar.setVerticalSpacing(4)
        self.fit_button = QPushButton("自适应")
        self.actual_size_button = QPushButton("1:1")
        self.zoom_out_button = QPushButton("−")
        self.zoom_in_button = QPushButton("＋")
        self.rotate_button = QPushButton("旋转")
        self.save_button = QPushButton("保存原图…")
        actions = (
            (self.fit_button, self.fit_to_view), (self.actual_size_button, self.set_actual_size),
            (self.zoom_out_button, self.zoom_out), (self.zoom_in_button, self.zoom_in),
            (self.rotate_button, self.rotate_clockwise), (self.save_button, lambda _checked=False: self.save_requested.emit()),
        )
        for index, (button, action) in enumerate(actions):
            button.clicked.connect(action)
            if index < 5:
                toolbar.addWidget(button, 0, index)
            else:
                toolbar.addWidget(button, 1, 0, 1, 2)
        self.effect_combo = QComboBox()
        for title, effect in IMAGE_EFFECT_LABELS:
            self.effect_combo.addItem(title, effect)
        self.effect_combo.setAccessibleName("图像显示效果")
        self.effect_combo.currentIndexChanged.connect(lambda _index: self.set_effect(self.effect_combo.currentData()))
        toolbar.addWidget(self.effect_combo, 1, 2, 1, 3)
        self.effect_param_label = QLabel("无参数")
        self.effect_param_label.setObjectName("Muted")
        self.effect_param_spin = QSpinBox()
        self.effect_param_spin.setAccessibleName("图像效果参数")
        self.effect_param_spin.valueChanged.connect(self.set_effect_parameter)
        toolbar.addWidget(self.effect_param_label, 2, 0)
        toolbar.addWidget(self.effect_param_spin, 2, 1, 1, 3)
        toolbar.setColumnStretch(5, 1)
        outer.addLayout(toolbar)
        self._update_effect_parameter_editor()
        self._canvas = _ImageCanvas()
        self._canvas.changed.connect(self._update_info)
        self._canvas.rotate_requested.connect(self.rotate_clockwise)
        outer.addWidget(self._canvas, 1)
        self.info_label = QLabel("未接收图像")
        self.info_label.setTextFormat(Qt.PlainText)
        self.info_label.setObjectName("Muted")
        self.info_label.setWordWrap(True)
        outer.addWidget(self.info_label)
        self._set_actions_enabled(False)

    @property
    def has_image(self) -> bool:
        return not self._original.isNull()

    @property
    def image_size(self) -> tuple[int, int]:
        return self._original.width(), self._original.height()

    @property
    def encoded_data(self) -> bytes:
        """Historical payload accessor; raw frames retain their original bytes."""
        return self._encoded

    @property
    def source_format(self) -> str:
        return self._source_format

    @property
    def default_suffix(self) -> str:
        return self._default_suffix

    @property
    def native_metadata(self) -> tuple[int, int, int] | None:
        return self._native_metadata

    @property
    def is_native_raw(self) -> bool:
        return self.has_image and not self._frame_encoded

    @property
    def rotation(self) -> int:
        return self._rotation

    @property
    def effect(self) -> str:
        return self._effect

    def _set_actions_enabled(self, enabled: bool) -> None:
        for widget in (self.fit_button, self.actual_size_button, self.zoom_out_button,
                       self.zoom_in_button, self.rotate_button, self.save_button):
            widget.setEnabled(enabled)

    @Slot(bytes)
    def set_encoded_image(self, encoded: bytes) -> bool:
        if not isinstance(encoded, bytes) or not (
                encoded.startswith(PNG_SIGNATURE) or encoded.startswith(JPEG_SIGNATURE)):
            self.error.emit("独立图像流只接受 JPEG / PNG 文件签名")
            return False
        format_id = 28 if encoded.startswith(PNG_SIGNATURE) else 27
        return self.set_native_frame(encoded, (1, 1, format_id))

    def set_native_frame(self, payload: bytes, metadata: tuple[int, int, int]) -> bool:
        try:
            frame = decode_native_frame(payload, metadata)
        except Exception as exc:
            self.error.emit(str(exc))
            return False
        return self.set_decoded_frame(frame, payload)

    def set_decoded_frame(self, frame: DecodedNativeFrame, payload: bytes) -> bool:
        """Show a frame decoded once by the shared store; no second decode."""
        if not isinstance(frame, DecodedNativeFrame) or frame.image.isNull():
            self.error.emit("图像解码失败")
            return False
        previous_size = self.image_size
        image = frame.image
        self._original, self._encoded = image, payload
        self._source_format = frame.source_format
        self._default_suffix = frame.default_suffix
        self._allowed_suffixes = frame.allowed_suffixes
        self._frame_encoded = frame.encoded
        self._native_metadata = frame.metadata
        self.save_button.setText("保存原图…" if frame.encoded else "保存视图PNG…")
        self._refresh_display()
        if previous_size != self.image_size:
            self._canvas.fit()
        self._set_actions_enabled(True)
        self._update_info()
        self.image_changed.emit(image.width(), image.height())
        return True

    def _refresh_display(self) -> None:
        try:
            result = apply_image_effect(self._original, self._effect, self._effect_parameter)
            image = result.image
            self._effect_preview_note = (
                f" · 显示预览缩小为 {result.preview_size[0]} × {result.preview_size[1]}（原始载荷未修改）"
                if result.reduced else ""
            )
        except Exception as exc:
            image = self._original
            self._effect_preview_note = " · 效果未应用，当前显示原图"
            self.error.emit(f"图像显示效果失败：{exc}")
        try:
            displayed = image if not self._rotation else image.transformed(QTransform().rotate(self._rotation))
            if not image.isNull() and displayed.isNull():
                raise ImageError("无法创建旋转后的显示图像")
        except Exception as exc:
            displayed = image
            self._effect_preview_note += " · 旋转未应用"
            self.error.emit(f"图像旋转显示失败：{exc}")
        self._canvas.image = displayed
        self._canvas.update()

    def _update_info(self) -> None:
        if not self.has_image:
            self.info_label.setText("未接收图像")
            return
        scale = self._canvas.display_scale()
        mode = "自适应" if self._canvas.scale is None else f"{scale * 100:.0f}%"
        spec = effect_parameter(self._effect)
        parameter = "" if self._effect == "none" else (
            f" {spec.label} {self._effect_parameter}{spec.suffix}"
        )
        self.info_label.setText(
            f"{self._source_format} · {self._original.width()} × {self._original.height()} · {len(self._encoded):,} 字节"
            f" · {mode} · 旋转 {self._rotation}° · {self.effect_combo.currentText()}{parameter}{self._effect_preview_note}"
        )

    def set_theme(self, theme: str) -> None:
        theme_palette(theme)
        self._theme = theme
        self._canvas.set_theme(theme)

    @property
    def effect_parameter(self) -> int:
        return self._effect_parameter

    def _update_effect_parameter_editor(self) -> None:
        spec = effect_parameter(self._effect)
        self.effect_param_label.setText(spec.label)
        if not spec.minimum <= self._effect_parameter <= spec.maximum:
            self._effect_parameter = spec.default
        blocked = self.effect_param_spin.blockSignals(True)
        self.effect_param_spin.setRange(spec.minimum, spec.maximum)
        self.effect_param_spin.setSuffix(spec.suffix)
        self.effect_param_spin.setValue(self._effect_parameter)
        self.effect_param_spin.setEnabled(self._effect != "none" and spec.maximum > spec.minimum)
        self.effect_param_spin.blockSignals(blocked)

    def set_effect_parameter(self, parameter: int) -> None:
        spec = effect_parameter(self._effect)
        if type(parameter) is not int or not spec.minimum <= parameter <= spec.maximum:
            raise ImageError(f"{spec.label}必须为{spec.minimum}–{spec.maximum}的整数")
        if parameter == self._effect_parameter:
            return
        self._effect_parameter = parameter
        if self.effect_param_spin.value() != parameter:
            blocked = self.effect_param_spin.blockSignals(True)
            self.effect_param_spin.setValue(parameter)
            self.effect_param_spin.blockSignals(blocked)
        self._refresh_display()
        self._update_info()

        self.configuration_changed.emit()

    def set_effect(self, effect: str) -> None:
        if not isinstance(effect, str) or effect not in IMAGE_EFFECTS:
            raise ImageError("图像效果不属于本软件已实现的显示效果")
        changed = effect != self._effect or self._effect_parameter != effect_parameter(effect).default
        self._effect = effect
        self._effect_parameter = effect_parameter(effect).default
        if self.effect_combo.currentData() != effect:
            blocked = self.effect_combo.blockSignals(True)
            self.effect_combo.setCurrentIndex(self.effect_combo.findData(effect))
            self.effect_combo.blockSignals(blocked)
        self._update_effect_parameter_editor()
        self._refresh_display()
        self._update_info()
        if changed:
            self.configuration_changed.emit()

    @Slot()
    def fit_to_view(self) -> None:
        self._canvas.fit()

    @Slot()
    def set_actual_size(self) -> None:
        self._canvas.scale = 1.0
        self._canvas.pan = QPointF()
        self._canvas.update()
        self._update_info()

    @Slot()
    def zoom_in(self) -> None:
        self._canvas.zoom(1.2)

    @Slot()
    def zoom_out(self) -> None:
        self._canvas.zoom(1 / 1.2)

    @Slot()
    def rotate_clockwise(self, *, notify: bool = True) -> None:
        if self.has_image:
            self._rotation = (self._rotation + 90) % 360
            self._canvas.pan = QPointF()
            self._refresh_display()
            self._update_info()
            if notify:
                self.configuration_changed.emit()

    @Slot()
    def clear(self) -> None:
        self._original = QImage()
        self._encoded = b""
        self._source_format = ""
        self._default_suffix = ".png"
        self._allowed_suffixes = frozenset()
        self._frame_encoded = True
        self._native_metadata = None
        self._effect_preview_note = ""
        self.save_button.setText("保存原图…")
        self._rotation = 0
        self._canvas.image = QImage()
        self._canvas.fit()
        self._set_actions_enabled(False)
        self._update_info()

    def save_image(self, path: str | Path) -> Path:
        if not self.has_image:
            raise ImageError("没有可保存的图像")
        destination = Path(path)
        if destination.suffix.lower() not in self._allowed_suffixes:
            raise ImageError(f"保存扩展名必须与 {self._source_format} 对应（{self._default_suffix}）")
        encoded = self._encoded
        if not self._frame_encoded:
            # Raw bytes have no encoded image-file header.  Export the current
            # view losslessly as PNG instead of merely renaming a pixel buffer.
            buffer = QBuffer()
            if not buffer.open(QIODevice.WriteOnly):
                raise ImageError("无法创建 PNG 内存输出")
            try:
                writer = QImageWriter(buffer, QByteArray(b"png"))
                if not writer.write(self._canvas.image):
                    raise ImageError("PNG 导出失败：" + writer.errorString())
                encoded = bytes(buffer.data())
                if len(encoded) > MAX_IMAGE_PIXELS * 4 + 1024 * 1024:
                    raise ImageError("PNG 导出超过有界像素预算")
            finally:
                buffer.close()
        # Exclusive creation: saving never overwrites an existing user file.
        with destination.open("xb") as stream:
            stream.write(encoded)
        self.status.emit(f"已保存{'原始图像' if self._frame_encoded else '当前图像视图 PNG'}：{destination}")
        return destination

    def save_raw_payload(self, path: str | Path) -> Path:
        """Explicitly preserve the input payload; never label it an image file."""
        if not self.has_image:
            raise ImageError("没有可保存的图像载荷")
        destination = Path(path)
        with destination.open("xb") as stream:
            stream.write(self._encoded)
        self.status.emit(f"已保存原始图像载荷（{len(self._encoded)}字节）：{destination}")
        return destination


def _natural_name(path: Path) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name.casefold()))


class ImageReplayWorker(QObject):
    """Explicit, read-only local image-directory playback on a QThread.

    Images are naturally filename-sorted and not recursively searched.  A bad
    image stops the run with an error instead of silently counting as a frame.
    Pausing suspends progression.  There is no serial/network/TX capability.
    """

    encoded = Signal(bytes)
    status = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal()
    events = Signal(object)  # Host compatibility only; never emits telemetry.
    raw = Signal(bytes)  # Host compatibility only; never emits hardware RX.
    transmitted = Signal(bytes)  # Host compatibility only; never sends.

    def __init__(self, path: str | Path, fps: float = 10.0, loop: bool = False, *, await_ack: bool = False) -> None:
        super().__init__()
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 0.1 <= fps <= 60:
            raise ValueError("图像回放帧率必须在 0.1–60 FPS 之间")
        if type(loop) is not bool:
            raise ValueError("图像循环设置必须为布尔值")
        if type(await_ack) is not bool:
            raise ValueError("图像交付背压设置必须为布尔值")
        self.path = Path(path)
        self.fps = float(fps)
        self.loop = loop
        self.await_ack = await_ack
        self._condition = threading.Condition()
        self._stopped = False
        self._paused = False
        self._started = False
        self._frame_pending = False

    @property
    def can_send(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return False

    def enqueue_send(self, _payload: bytes) -> int:
        raise ImageError("本地图像回放不具备发送能力")

    def clear_pending(self) -> int:
        return 0

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("暂停状态必须为布尔值")
        with self._condition:
            self._paused = paused
            self._condition.notify_all()
            started = self._started
        if started:
            self.status.emit("paused" if paused else "playback", "图像回放已暂停" if paused else "图像回放继续")

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def ack_frame(self) -> None:
        """Acknowledge GUI consumption; safe to call directly across threads."""
        with self._condition:
            self._frame_pending = False
            self._condition.notify_all()

    acknowledge_frame = ack_frame

    def _deliver(self, encoded: bytes) -> bool:
        with self._condition:
            if self._stopped:
                return False
            self._frame_pending = self.await_ack
        self.encoded.emit(encoded)
        if self.await_ack:
            with self._condition:
                while self._frame_pending and not self._stopped:
                    self._condition.wait(0.1)
                return not self._stopped
        return True

    def _wait(self, delay: float) -> bool:
        remaining = max(0.0, delay)
        with self._condition:
            while not self._stopped:
                if self._paused:
                    self._condition.wait(0.1)
                    continue
                if remaining <= 0:
                    return True
                before = time.monotonic()
                self._condition.wait(min(remaining, 0.1))
                remaining -= max(0.0, time.monotonic() - before)
            return False

    def _inventory(self) -> tuple[Path, list[Path]]:
        if self.path.is_symlink() or self.path.is_junction():
            raise ImageError("图像回放目录不能是符号链接或目录联接")
        root = self.path.resolve(strict=True)
        if not root.is_dir():
            raise ImageError("请选择图像目录")
        files: list[Path] = []
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_ENTRIES:
                    raise ImageError("目录条目超过 50000，请选择更小的图像目录")
                if self._stopped:
                    return root, []
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                item = Path(entry.path)
                if item.suffix.lower() in _IMAGE_SUFFIXES:
                    files.append(item)
                    if len(files) > MAX_DIRECTORY_IMAGES:
                        raise ImageError("图像目录超过 10000 张上限")
        if not files and not self._stopped:
            raise ImageError("所选目录没有 JPEG / PNG 图像")
        return root, sorted(files, key=_natural_name)

    @staticmethod
    def _read_image(path: Path, root: Path) -> bytes:
        if path.is_symlink() or path.is_junction() or path.resolve(strict=True).parent != root:
            raise ImageError("回放图像已变为链接或移动到目录之外")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_IMAGE_BYTES:
            raise ImageError("回放图像必须为非空普通文件且不超过 8 MiB")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not 1 <= opened.st_size <= MAX_IMAGE_BYTES:
                raise ImageError("打开后的图像文件类型或大小无效")
            if before.st_ino and opened.st_ino and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ImageError("图像文件在打开时被替换")
            encoded = stream.read(MAX_IMAGE_BYTES + 1)
        if len(encoded) > MAX_IMAGE_BYTES:
            raise ImageError("图像文件在回放时增长超过 8 MiB")
        decode_image(encoded)
        return encoded

    @Slot()
    def run(self) -> None:
        with self._condition:
            if self._started:
                self.status.emit("error", "图像回放实例只能运行一次")
                self.finished.emit()
                return
            self._started = True
        try:
            root, files = self._inventory()
            if self._stopped:
                self.status.emit("stopped", "图像回放已停止")
                return
            self.status.emit("playback", f"图像回放：{len(files)} 张，{self.fps:g} FPS；未连接设备")
            while not self._stopped:
                self.progress.emit(0, len(files))
                for index, path in enumerate(files):
                    if not self._wait(0):
                        break
                    frame_at = time.monotonic()
                    encoded = self._read_image(path, root)
                    if self._stopped:
                        break
                    if not self._deliver(encoded):
                        break
                    self.progress.emit(index + 1, len(files))
                    delay = max(0.0, 1.0 / self.fps - (time.monotonic() - frame_at))
                    if not self._wait(delay):
                        break
                if self._stopped:
                    self.status.emit("stopped", "图像回放已停止")
                    return
                if not self.loop:
                    self.status.emit("completed", "图像目录回放完成；未连接或烧录设备")
                    return
            self.status.emit("stopped", "图像回放已停止")
        except Exception as exc:
            self.status.emit("error", f"图像回放停止：{exc}")
        finally:
            self.finished.emit()
