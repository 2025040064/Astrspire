"""Own bounded display filters, unrelated to a proprietary shader pipeline.

CPU filters operate on a reduced preview of at most 16,384 pixels with longest
edge 256.  They never alter the caller's source QImage or encoded input payload.
No imports or construction perform file, network or device access.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage


IMAGE_EFFECT_LABELS: tuple[tuple[str, str], ...] = (
    ("原图", "none"), ("灰度", "grayscale"), ("反色", "invert"),
    ("黑白", "threshold"), ("像素化", "pixelate"),
    ("模糊", "blur"), ("锐化", "sharpen"), ("边缘", "edges"),
    ("浮雕", "emboss"), ("复古", "sepia"),
    ("海报化", "posterize"), ("卡通", "toon"), ("泛光", "glow"),
    ("暗角", "vignette"), ("扫描线", "billboard"), ("波普", "warhol"),
    ("移轴模糊", "tilt_shift"), ("色相隔离", "isolate"),
    ("冲击波", "shockwave"), ("摆动", "wobble"),
)
IMAGE_EFFECTS = frozenset(effect for _label, effect in IMAGE_EFFECT_LABELS)
CPU_IMAGE_EFFECTS = frozenset({"blur", "sharpen", "edges", "emboss", "sepia",
                               "posterize", "toon", "glow", "vignette", "billboard",
                               "warhol", "tilt_shift", "isolate", "shockwave", "wobble"})
MAX_EFFECT_PIXELS = 16_384
MAX_EFFECT_EDGE = 256


@dataclass(frozen=True, slots=True)
class EffectParameter:
    label: str
    minimum: int
    maximum: int
    default: int
    suffix: str = ""


EFFECT_PARAMETERS: dict[str, EffectParameter] = {
    "none": EffectParameter("无参数", 0, 0, 0),
    "grayscale": EffectParameter("强度", 0, 100, 100, "%"),
    "invert": EffectParameter("强度", 0, 100, 100, "%"),
    "threshold": EffectParameter("阈值", 0, 255, 128),
    "pixelate": EffectParameter("块大小", 2, 64, 8, "px"),
    "blur": EffectParameter("强度", 0, 100, 100, "%"),
    "sharpen": EffectParameter("强度", 0, 100, 100, "%"),
    "edges": EffectParameter("强度", 0, 100, 100, "%"),
    "emboss": EffectParameter("强度", 0, 100, 100, "%"),
    "sepia": EffectParameter("强度", 0, 100, 100, "%"),
    "posterize": EffectParameter("伽马", 0, 100, 50),
    "toon": EffectParameter("阈值", 0, 100, 50),
    "glow": EffectParameter("强度", 0, 100, 60, "%"),
    "vignette": EffectParameter("强度", 0, 100, 60, "%"),
    "billboard": EffectParameter("网格间距", 0, 100, 50),
    "warhol": EffectParameter("强度", 0, 100, 60, "%"),
    "tilt_shift": EffectParameter("强度", 0, 100, 60, "%"),
    "isolate": EffectParameter("色相", 0, 255, 128),
    "shockwave": EffectParameter("幅度", 0, 100, 50, "%"),
    "wobble": EffectParameter("幅度", 0, 100, 50, "%"),
}


def effect_parameter(effect: str) -> EffectParameter:
    try:
        return EFFECT_PARAMETERS[effect]
    except KeyError as exc:
        raise ValueError("不支持的图像显示效果") from exc


@dataclass(frozen=True, slots=True)
class EffectPreview:
    image: QImage
    source_size: tuple[int, int]
    preview_size: tuple[int, int]
    reduced: bool


def _preview_source(image: QImage) -> QImage:
    width, height = image.width(), image.height()
    factor = min(1.0, MAX_EFFECT_EDGE / width, MAX_EFFECT_EDGE / height,
                 math.sqrt(MAX_EFFECT_PIXELS / (width * height)))
    if factor < 1.0:
        # Floor both dimensions so the pixel budget cannot be exceeded by
        # independent rounding.  Aspect ratio remains a display approximation.
        target_width = max(1, min(MAX_EFFECT_EDGE, int(width * factor)))
        target_height = max(1, min(MAX_EFFECT_EDGE, int(height * factor)))
        image = image.scaled(target_width, target_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return image.convertToFormat(QImage.Format_RGBA8888)


def _clamp(value: int) -> int:
    return 0 if value < 0 else 255 if value > 255 else value


def _sepia(source: bytes, output: bytearray, width: int, height: int, stride: int) -> None:
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            red, green, blue = source[at], source[at + 1], source[at + 2]
            output[at] = min(255, (393 * red + 769 * green + 189 * blue) // 1000)
            output[at + 1] = min(255, (349 * red + 686 * green + 168 * blue) // 1000)
            output[at + 2] = min(255, (272 * red + 534 * green + 131 * blue) // 1000)


def _convolve(source: bytes, output: bytearray, width: int, height: int,
              stride: int, effect: str) -> None:
    kernels = {
        "blur": ((1, 1, 1, 1, 1, 1, 1, 1, 1), 9, 0),
        "sharpen": ((0, -1, 0, -1, 5, -1, 0, -1, 0), 1, 0),
        "edges": ((-1, -1, -1, -1, 8, -1, -1, -1, -1), 1, 0),
        "emboss": ((-2, -1, 0, -1, 0, 1, 0, 1, 2), 1, 128),
    }
    kernel, divisor, bias = kernels[effect]
    taps = tuple((index % 3 - 1, index // 3 - 1, weight)
                 for index, weight in enumerate(kernel) if weight)
    # Read from one stable source, never from pixels already written to output.
    # Repeating the edge pixel keeps the filter defined for 1-pixel dimensions.
    for y in range(height):
        for x in range(width):
            red = green = blue = 0
            for dx, dy, weight in taps:
                sample_x = min(width - 1, max(0, x + dx))
                sample_y = min(height - 1, max(0, y + dy))
                at = sample_y * stride + sample_x * 4
                red += source[at] * weight
                green += source[at + 1] * weight
                blue += source[at + 2] * weight
            if effect == "edges":
                red, green, blue = abs(red), abs(green), abs(blue)
            destination = y * stride + x * 4
            output[destination] = _clamp(red // divisor + bias)
            output[destination + 1] = _clamp(green // divisor + bias)
            output[destination + 2] = _clamp(blue // divisor + bias)


def _blend(source: bytes, output: bytearray, strength: int,
           width: int, height: int, stride: int) -> None:
    if strength >= 100:
        return
    if strength <= 0:
        output[:] = source
        return
    inverse = 100 - strength
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            for channel in range(3):
                output[at + channel] = (source[at + channel] * inverse + output[at + channel] * strength) // 100


def _luma(red: int, green: int, blue: int) -> int:
    return (red * 77 + green * 150 + blue * 29) >> 8


def _box_blur(source: bytes, width: int, height: int, stride: int, radius: int) -> bytearray:
    """Clamped two-dimensional box blur; bounded by the CPU preview budget."""
    result = bytearray(len(source))
    span = radius * 2 + 1
    for y in range(height):
        top, bottom = max(0, y - radius), min(height - 1, y + radius)
        row = y * stride
        for x in range(width):
            left, right = max(0, x - radius), min(width - 1, x + radius)
            red = green = blue = alpha = 0
            for sample_y in range(top, bottom + 1):
                base = sample_y * stride
                for sample_x in range(left, right + 1):
                    at = base + sample_x * 4
                    red += source[at]
                    green += source[at + 1]
                    blue += source[at + 2]
                    alpha += source[at + 3]
            count = (bottom - top + 1) * (right - left + 1)
            at = row + x * 4
            result[at] = red // count
            result[at + 1] = green // count
            result[at + 2] = blue // count
            result[at + 3] = alpha // count
    return result


def _posterize(source: bytes, output: bytearray, width: int, height: int,
               stride: int, parameter: int) -> None:
    gamma = max(0.05, parameter / 100.0)
    levels = 8
    table = [round(round(((value / 255.0) ** gamma) * (levels - 1)) * 255 / (levels - 1))
             for value in range(256)]
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            output[at] = table[source[at]]
            output[at + 1] = table[source[at + 1]]
            output[at + 2] = table[source[at + 2]]


def _toon(source: bytes, output: bytearray, width: int, height: int,
          stride: int, parameter: int) -> None:
    levels = 5
    edge_limit = 6 + parameter * 3 // 10
    for y in range(height):
        row = y * stride
        next_row = min(height - 1, y + 1) * stride
        for x in range(width):
            at = row + x * 4
            right = row + min(width - 1, x + 1) * 4
            below = next_row + x * 4
            red, green, blue = source[at], source[at + 1], source[at + 2]
            quantized = [min(255, (value * levels // 256) * 255 // (levels - 1)) for value in (red, green, blue)]
            gradient = (abs(_luma(red, green, blue) - _luma(source[right], source[right + 1], source[right + 2]))
                        + abs(_luma(red, green, blue) - _luma(source[below], source[below + 1], source[below + 2])))
            if gradient > edge_limit:
                quantized = [value // 4 for value in quantized]
            output[at], output[at + 1], output[at + 2] = quantized


def _glow(source: bytes, output: bytearray, width: int, height: int,
          stride: int, parameter: int) -> None:
    bright = bytearray(len(source))
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            value = max(source[at], source[at + 1], source[at + 2])
            if value > 150:
                scale = (value - 150) / 105.0
                bright[at] = int(source[at] * scale)
                bright[at + 1] = int(source[at + 1] * scale)
                bright[at + 2] = int(source[at + 2] * scale)
                bright[at + 3] = 255
    blurred = _box_blur(bright, width, height, stride, 3)
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            for channel in range(3):
                output[at + channel] = min(255, source[at + channel] + blurred[at + channel] * parameter // 100)


def _vignette(source: bytes, output: bytearray, width: int, height: int,
              stride: int, parameter: int) -> None:
    center_x, center_y = (width - 1) / 2, (height - 1) / 2
    limit = math.hypot(center_x, center_y) or 1.0
    strength = parameter / 100.0
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            distance = math.hypot(x - center_x, y - center_y) / limit
            factor = 1.0 - strength * distance * distance
            output[at] = int(source[at] * factor)
            output[at + 1] = int(source[at + 1] * factor)
            output[at + 2] = int(source[at + 2] * factor)


def _billboard(source: bytes, output: bytearray, width: int, height: int,
               stride: int, parameter: int) -> None:
    spacing = 2 + parameter * 10 // 100
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            factor = 1.0
            if x % spacing == 0 or y % spacing == 0:
                factor = 0.62
            elif y % 2:
                factor = 0.9
            output[at] = int(source[at] * factor)
            output[at + 1] = int(source[at + 1] * factor)
            output[at + 2] = int(source[at + 2] * factor)


def _hue_rotate(red: int, green: int, blue: int, shift: float) -> tuple[int, int, int]:
    hue, lightness, saturation = colorsys.rgb_to_hls(red / 255.0, green / 255.0, blue / 255.0)
    rotated = colorsys.hls_to_rgb((hue + shift) % 1.0, lightness, min(1.0, saturation * 1.15 + 0.05))
    return tuple(int(value * 255) for value in rotated)


def _warhol(source: bytes, output: bytearray, width: int, height: int,
            stride: int, parameter: int) -> None:
    shifts = (0.0, 0.25, 0.5, 0.75)
    for y in range(height):
        row = y * stride
        quadrant_y = 1 if y >= height / 2 else 0
        for x in range(width):
            at = row + x * 4
            quadrant = quadrant_y * 2 + (1 if x >= width / 2 else 0)
            red, green, blue = _hue_rotate(source[at], source[at + 1], source[at + 2], shifts[quadrant])
            output[at], output[at + 1], output[at + 2] = red, green, blue
    _blend(source, output, parameter, width, height, stride)


def _tilt_shift(source: bytes, output: bytearray, width: int, height: int,
                stride: int, parameter: int) -> None:
    blurred = _box_blur(source, width, height, stride, 3)
    center_y = (height - 1) / 2
    band = max(1.0, height * 0.12)
    spread = max(1.0, height * 0.5 - band)
    strength = parameter / 100.0
    for y in range(height):
        row = y * stride
        weight = min(1.0, max(0.0, (abs(y - center_y) - band) / spread)) * strength
        for x in range(width):
            at = row + x * 4
            for channel in range(3):
                output[at + channel] = int(source[at + channel] * (1.0 - weight) + blurred[at + channel] * weight)


def _isolate(source: bytes, output: bytearray, width: int, height: int,
             stride: int, parameter: int) -> None:
    target = parameter / 255.0 * 360.0
    window = 30.0
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            red, green, blue = source[at], source[at + 1], source[at + 2]
            hue = colorsys.rgb_to_hls(red / 255.0, green / 255.0, blue / 255.0)[0] * 360.0
            distance = min(abs(hue - target), 360.0 - abs(hue - target))
            if distance > window:
                gray = _luma(red, green, blue)
                output[at] = output[at + 1] = output[at + 2] = gray
            else:
                boost = 1.0 - distance / window
                output[at] = _clamp(int(red + (red - _luma(red, green, blue)) * boost * 0.4))
                output[at + 1] = _clamp(int(green + (green - _luma(red, green, blue)) * boost * 0.4))
                output[at + 2] = _clamp(int(blue + (blue - _luma(red, green, blue)) * boost * 0.4))


def _shockwave(source: bytes, output: bytearray, width: int, height: int,
               stride: int, parameter: int) -> None:
    center_x, center_y = (width - 1) / 2, (height - 1) / 2
    limit = math.hypot(center_x, center_y) or 1.0
    amplitude = parameter / 100.0 * min(width, height) * 0.04
    for y in range(height):
        row = y * stride
        for x in range(width):
            dx, dy = x - center_x, y - center_y
            distance = math.hypot(dx, dy)
            if distance < 1e-6:
                sample_x, sample_y = x, y
            else:
                shift = amplitude * math.sin(distance * 0.35) * (1.0 - distance / limit)
                sample_x = int(round(x + dx / distance * shift))
                sample_y = int(round(y + dy / distance * shift))
            sample_x = min(width - 1, max(0, sample_x))
            sample_y = min(height - 1, max(0, sample_y))
            source_at = sample_y * stride + sample_x * 4
            at = row + x * 4
            output[at:at + 4] = source[source_at:source_at + 4]


def _wobble(source: bytes, output: bytearray, width: int, height: int,
            stride: int, parameter: int) -> None:
    amplitude = parameter / 100.0 * width * 0.04
    for y in range(height):
        row = y * stride
        shift = int(round(amplitude * math.sin(y / max(1, height) * math.pi * 4)))
        for x in range(width):
            sample_x = min(width - 1, max(0, x - shift))
            at = row + x * 4
            source_at = row + sample_x * 4
            output[at:at + 4] = source[source_at:source_at + 4]


def _preview_output(image: QImage) -> tuple[bytes, bytearray, int, int, int]:
    preview = _preview_source(image)
    if preview.isNull():
        raise ValueError("无法创建有界图像预览")
    width, height, stride = preview.width(), preview.height(), preview.bytesPerLine()
    if width * height > MAX_EFFECT_PIXELS or max(width, height) > MAX_EFFECT_EDGE:
        raise ValueError("图像显示效果超出 CPU 预览预算")
    source = bytes(preview.constBits())
    return source, bytearray(source), width, height, stride


def apply_image_effect(image: QImage, effect: str, parameter: int | None = None) -> EffectPreview:
    """Return an independent display result plus explicit reduction metadata.

    ``parameter`` is a self-owned display control (strength, threshold or block
    size).  Its ranges are declared by ``EFFECT_PARAMETERS``; it is not claimed
    to match a native VOFA+ shader parameter.
    """
    if not isinstance(effect, str) or effect not in IMAGE_EFFECTS:
        raise ValueError("不支持的图像显示效果")
    if not isinstance(image, QImage):
        raise TypeError("图像显示效果输入必须为 QImage")
    spec = effect_parameter(effect)
    if parameter is None:
        parameter = spec.default
    if type(parameter) is not int or not spec.minimum <= parameter <= spec.maximum:
        raise ValueError(f"{spec.label}必须为{spec.minimum}–{spec.maximum}的整数")
    source_size = (image.width(), image.height())
    if image.isNull() or effect == "none":
        return EffectPreview(image, source_size, source_size, False)
    if effect not in {"threshold", "pixelate", "posterize", "toon", "isolate"} and parameter <= 0:
        return EffectPreview(image, source_size, source_size, False)
    if effect == "pixelate":
        block = parameter
        tiny = image.scaled(max(1, image.width() // block), max(1, image.height() // block),
                            Qt.IgnoreAspectRatio, Qt.FastTransformation)
        result = tiny.scaled(image.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation)
    elif effect == "threshold":
        source, output, width, height, stride = _preview_output(image)
        for y in range(height):
            row = y * stride
            for x in range(width):
                at = row + x * 4
                gray = (source[at] * 77 + source[at + 1] * 150 + source[at + 2] * 29) >> 8
                value = 255 if gray >= parameter else 0
                output[at] = output[at + 1] = output[at + 2] = value
        result = QImage(bytes(output), width, height, stride, QImage.Format_RGBA8888).copy()
    elif effect in {"grayscale", "invert"}:
        if parameter >= 100:
            result = (image.convertToFormat(QImage.Format_Grayscale8) if effect == "grayscale"
                      else image.copy())
            if effect == "invert":
                result.invertPixels()
        else:
            source, output, width, height, stride = _preview_output(image)
            for y in range(height):
                row = y * stride
                for x in range(width):
                    at = row + x * 4
                    if effect == "grayscale":
                        gray = (source[at] * 77 + source[at + 1] * 150 + source[at + 2] * 29) >> 8
                        output[at] = output[at + 1] = output[at + 2] = gray
                    else:
                        output[at] = 255 - source[at]
                        output[at + 1] = 255 - source[at + 1]
                        output[at + 2] = 255 - source[at + 2]
            _blend(source, output, parameter, width, height, stride)
            result = QImage(bytes(output), width, height, stride, QImage.Format_RGBA8888).copy()
    else:
        source, output, width, height, stride = _preview_output(image)
        filters = {
            "posterize": _posterize, "toon": _toon, "glow": _glow,
            "vignette": _vignette, "billboard": _billboard, "warhol": _warhol,
            "tilt_shift": _tilt_shift, "isolate": _isolate,
            "shockwave": _shockwave, "wobble": _wobble,
        }
        if effect == "sepia":
            _sepia(source, output, width, height, stride)
            _blend(source, output, parameter, width, height, stride)
        elif effect in filters:
            filters[effect](source, output, width, height, stride, parameter)
        else:
            _convolve(source, output, width, height, stride, effect)
            _blend(source, output, parameter, width, height, stride)
        result = QImage(bytes(output), width, height, stride, QImage.Format_RGBA8888).copy()
    if result.isNull():
        raise ValueError("图像显示效果处理失败")
    preview_size = (result.width(), result.height())
    return EffectPreview(result, source_size, preview_size, preview_size != source_size)
