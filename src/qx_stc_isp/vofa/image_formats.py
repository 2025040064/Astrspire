"""Bounded display adapters for observed native image format identifiers.

The 25..34 dispatch is static evidence from VOFA+ 1.3.10's receiver switch
(`ev_cd686bbd28b65d8ebf1639ea61711c62c14abdee7792edccd56510c99aac4109`).
Using Qt6's numeric QImage.Format values for the receiver's Qt5 raw formats is
a compatibility assumption, not a claim of runtime or hardware verification.
The SVG adapter deliberately admits only a constrained static subset.

Importing this module performs no file, network, plugin-probing or image I/O.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_RAW_DIMENSION = 16_384
MAX_SVG_BYTES = 1024 * 1024
MAX_SVG_NODES = 2048
MAX_SVG_DEPTH = 32
MAX_SVG_PATH_CHARS = 262_144
MAX_SVG_TEXT_CHARS = 32_768
MAX_SVG_ATTRIBUTE_CHARS = 65_536
MAX_SVG_REFERENCES = 4096
MAX_SVG_EXPANDED_NODES = 10_000


class ImageFormatError(ValueError):
    """An encoded/native image is unsupported or exceeds an input budget."""


@dataclass(frozen=True, slots=True)
class EncodedFormat:
    name: str
    reader_format: bytes
    suffix: str
    allowed_suffixes: frozenset[str]


ENCODED_FORMATS: dict[int, EncodedFormat] = {
    25: EncodedFormat("BMP", b"bmp", ".bmp", frozenset({".bmp"})),
    26: EncodedFormat("GIF", b"gif", ".gif", frozenset({".gif"})),
    27: EncodedFormat("JPG", b"jpeg", ".jpg", frozenset({".jpg", ".jpeg"})),
    28: EncodedFormat("PNG", b"png", ".png", frozenset({".png"})),
    29: EncodedFormat("PBM", b"pbm", ".pbm", frozenset({".pbm"})),
    30: EncodedFormat("PGM", b"pgm", ".pgm", frozenset({".pgm"})),
    31: EncodedFormat("PPM", b"ppm", ".ppm", frozenset({".ppm"})),
    32: EncodedFormat("XBM", b"xbm", ".xbm", frozenset({".xbm"})),
    33: EncodedFormat("XPM", b"xpm", ".xpm", frozenset({".xpm"})),
    34: EncodedFormat("SVG", b"svg", ".svg", frozenset({".svg"})),
}


@dataclass(frozen=True, slots=True)
class DecodedNativeFrame:
    image: QImage
    source_format: str
    default_suffix: str
    encoded: bool
    allowed_suffixes: frozenset[str]
    metadata: tuple[int, int, int]


_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_SVG_TAGS = frozenset({
    "svg", "g", "defs", "symbol", "use", "path", "rect", "circle", "ellipse",
    "line", "polyline", "polygon", "text", "tspan", "linearGradient",
    "radialGradient", "stop", "clipPath", "title", "desc",
})
_SVG_ATTRIBUTES = frozenset({
    "id", "version", "viewBox", "x", "y", "width", "height", "x1", "x2", "y1",
    "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "fill-opacity",
    "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
    "opacity", "transform", "display", "visibility", "preserveAspectRatio", "offset",
    "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod",
    "fx", "fy", "fr", "text-anchor", "font-family", "font-size", "font-style",
    "font-weight", "letter-spacing", "word-spacing", "dominant-baseline", "clip-path",
    "clip-rule", "href", "style", "space",
})
_SVG_STYLE_PROPERTIES = frozenset({
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray",
    "stroke-dashoffset", "opacity", "display", "visibility", "stop-color", "stop-opacity",
    "text-anchor", "font-family", "font-size", "font-style", "font-weight",
    "letter-spacing", "word-spacing", "dominant-baseline", "clip-path", "clip-rule",
})
_SVG_GEOMETRY_ATTRIBUTES = frozenset({
    "viewBox", "x", "y", "width", "height", "x1", "x2", "y1", "y2", "cx", "cy",
    "r", "rx", "ry", "d", "points", "transform", "gradientTransform", "fx", "fy",
    "fr", "font-size", "letter-spacing", "word-spacing", "stroke-width",
    "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
})
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_URL = re.compile(r"\burl\s*\(([^)]*)\)", re.IGNORECASE)
_URL_OPEN = re.compile(r"\burl\s*\(", re.IGNORECASE)
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _name(value: str) -> tuple[str, str]:
    if value.startswith("{"):
        namespace, _, local = value[1:].partition("}")
        return namespace, local
    return "", value


def _svg_value(value: str) -> list[str]:
    lowered = value.casefold()
    if ("\\" in value or "@import" in lowered or "/*" in value or "*/" in value
            or "javascript:" in lowered or "file:" in lowered or "data:" in lowered
            or "://" in value or "expression(" in lowered):
        raise ImageFormatError("SVG 含外部资源、转义 CSS 或不支持的活动内容")
    opens = list(_URL_OPEN.finditer(value))
    matches = list(_URL.finditer(value))
    if len(opens) != len(matches):
        raise ImageFormatError("SVG 的 url() 语法不受支持")
    references = []
    for match in matches:
        target = match.group(1).strip().strip("\"'").strip()
        if not target.startswith("#") or _ID.fullmatch(target[1:]) is None:
            raise ImageFormatError("SVG url() 只允许文档内部的 #id")
        references.append(target[1:])
    return references


def validate_static_svg(payload: bytes) -> None:
    """Admit a bounded static SVG subset before any Qt SVG decoding.

    The allowlist excludes scripts, animation, foreignObject, images, stylesheets,
    filters and external fonts.  Internal references are checked for cycles and
    excessive expansion.  Unsupported safe SVG features are also rejected: this
    is a safety boundary, not a complete SVG implementation.
    """
    if len(payload) > MAX_SVG_BYTES:
        raise ImageFormatError("SVG 超过本软件安全子集的 1 MiB 上限")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ImageFormatError("SVG 安全子集仅接受 UTF-8 编码") from exc
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ImageFormatError("SVG 不允许 DOCTYPE 或 ENTITY")
    if "@import" in text.casefold():
        raise ImageFormatError("SVG 不允许 @import")
    for instruction in re.findall(r"<\?.*?\?>", text, re.DOTALL):
        if not re.fullmatch(r"<\?xml\s+[^?]*\?>", instruction, re.IGNORECASE):
            raise ImageFormatError("SVG 不允许样式表或其他处理指令")
        encoding = re.search(r"\bencoding\s*=\s*['\"]([^'\"]+)['\"]", instruction, re.IGNORECASE)
        if encoding and encoding.group(1).casefold() not in {"utf-8", "utf8", "us-ascii", "ascii"}:
            raise ImageFormatError("SVG 编码声明不属于 UTF-8/ASCII 安全子集")
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError) as exc:
        raise ImageFormatError(f"SVG XML 无效：{exc}") from exc
    if _name(root.tag)[1] != "svg":
        raise ImageFormatError("SVG 根元素必须为 svg")
    nodes: list[ET.Element] = []
    ids: dict[str, int] = {}
    references: list[list[str]] = []
    stack = [(root, 1)]
    path_chars = text_chars = reference_count = 0
    while stack:
        node, depth = stack.pop()
        if depth > MAX_SVG_DEPTH or len(nodes) >= MAX_SVG_NODES:
            raise ImageFormatError("SVG 节点数或嵌套深度超过安全上限")
        namespace, tag = _name(node.tag)
        if namespace not in {"", _SVG_NAMESPACE} or tag not in _SVG_TAGS:
            raise ImageFormatError(f"SVG 元素 {tag} 不属于受支持的静态安全子集")
        index = len(nodes)
        nodes.append(node)
        own_references: list[str] = []
        text_chars += len(node.text or "") + len(node.tail or "")
        if text_chars > MAX_SVG_TEXT_CHARS or len(node.attrib) > 64:
            raise ImageFormatError("SVG 文本或单节点属性数超过安全上限")
        for raw_attribute, value in node.attrib.items():
            attr_namespace, attribute = _name(raw_attribute)
            if (attr_namespace not in {"", _XLINK_NAMESPACE, _XML_NAMESPACE}
                    or attr_namespace == _XLINK_NAMESPACE and attribute != "href"
                    or attr_namespace == _XML_NAMESPACE and attribute != "space"):
                raise ImageFormatError("SVG 含不支持的命名空间属性")
            if attribute.casefold().startswith("on") or attribute not in _SVG_ATTRIBUTES:
                raise ImageFormatError(f"SVG 属性 {attribute} 不属于静态安全子集")
            if len(value) > MAX_SVG_ATTRIBUTE_CHARS:
                raise ImageFormatError("SVG 单属性内容超过安全上限")
            own_references.extend(_svg_value(value))
            if attribute == "id":
                if _ID.fullmatch(value) is None or value in ids:
                    raise ImageFormatError("SVG id 无效、过长或重复")
                ids[value] = index
            elif attribute == "href":
                if not value.startswith("#") or _ID.fullmatch(value[1:]) is None:
                    raise ImageFormatError("SVG href 只允许文档内部的 #id")
                own_references.append(value[1:])
            elif attribute == "style":
                for declaration in value.split(";"):
                    if not declaration.strip():
                        continue
                    property_name, separator, property_value = declaration.partition(":")
                    property_name = property_name.strip().casefold()
                    if not separator or property_name not in _SVG_STYLE_PROPERTIES:
                        raise ImageFormatError("SVG 内联样式含不支持的属性")
                    if property_name in _SVG_GEOMETRY_ATTRIBUTES:
                        for number in _NUMBER.findall(property_value):
                            numeric = float(number)
                            if not math.isfinite(numeric) or abs(numeric) > 1e9:
                                raise ImageFormatError("SVG 数值无效或过大")
            if attribute in {"d", "points"}:
                path_chars += len(value)
                if path_chars > MAX_SVG_PATH_CHARS:
                    raise ImageFormatError("SVG 路径内容超过安全上限")
            if attribute in _SVG_GEOMETRY_ATTRIBUTES:
                for number in _NUMBER.findall(value):
                    numeric = float(number)
                    if not math.isfinite(numeric) or abs(numeric) > 1e9:
                        raise ImageFormatError("SVG 几何数值无效或过大")
        reference_count += len(own_references)
        if reference_count > MAX_SVG_REFERENCES:
            raise ImageFormatError("SVG 内部引用过多")
        references.append(own_references)
        stack.extend((child, depth + 1) for child in reversed(list(node)))
    indexes = {id(node): index for index, node in enumerate(nodes)}
    edges: list[list[int]] = []
    for index, node in enumerate(nodes):
        targets = [indexes[id(child)] for child in node]
        for reference in references[index]:
            if reference not in ids:
                raise ImageFormatError("SVG 内部引用指向不存在的 id")
            targets.append(ids[reference])
        edges.append(targets)
    # Iterative DFS avoids Python recursion on a long chain of internal uses.
    colours = [0] * len(nodes)
    costs = [0] * len(nodes)
    expansion_depths = [0] * len(nodes)
    for start in range(len(nodes)):
        if colours[start]:
            continue
        walk = [(start, False)]
        while walk:
            index, exiting = walk.pop()
            if exiting:
                cost = 1 + sum(costs[target] for target in edges[index])
                if cost > MAX_SVG_EXPANDED_NODES:
                    raise ImageFormatError("SVG 内部引用展开超过安全上限")
                expansion_depth = 1 + max((expansion_depths[target] for target in edges[index]), default=0)
                if expansion_depth > MAX_SVG_DEPTH:
                    raise ImageFormatError("SVG 内部引用展开深度超过安全上限")
                expansion_depths[index] = expansion_depth
                costs[index], colours[index] = cost, 2
                continue
            if colours[index] == 1:
                raise ImageFormatError("SVG 内部引用形成循环")
            if colours[index] == 2:
                continue
            colours[index] = 1
            walk.append((index, True))
            for target in reversed(edges[index]):
                if colours[target] == 1:
                    raise ImageFormatError("SVG 内部引用形成循环")
                if not colours[target]:
                    walk.append((target, False))


def _payload(payload: bytes) -> None:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_IMAGE_BYTES:
        raise ImageFormatError("图像数据必须为非空 bytes，且不超过 8 MiB")


def _pixel_budget(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ImageFormatError("实际图像尺寸无效或超过 1600 万像素")


def _decode_encoded(payload: bytes, specification: EncodedFormat) -> QImage:
    if specification.name == "SVG":
        validate_static_svg(payload)
    buffer = QBuffer()
    buffer.setData(QByteArray(payload))
    if not buffer.open(QIODevice.ReadOnly):
        raise ImageFormatError("无法读取图像内存数据")
    try:
        reader = QImageReader(buffer, QByteArray(specification.reader_format))
        reader.setAutoDetectImageFormat(False)
        reader.setDecideFormatFromContent(False)
        reader.setAutoTransform(True)
        if not reader.canRead():
            raise ImageFormatError(f"{specification.name} 图像无效，或当前 Qt 未提供可用格式插件：{reader.errorString()}")
        size = reader.size()
        _pixel_budget(size.width(), size.height())
        image = reader.read()
        if image.isNull():
            raise ImageFormatError(f"{specification.name} 图像解码失败：{reader.errorString()}")
        _pixel_budget(image.width(), image.height())
        return image
    finally:
        buffer.close()


def decode_native_frame(payload: bytes, metadata: tuple[int, int, int]) -> DecodedNativeFrame:
    """Decode an observed encoded identifier or a bounded Qt raw image.

    Raw rows use the receiver constructor's implied four-byte alignment, made
    explicit here.  Qt5/Qt6 raw enum numeric correspondence remains an untested
    compatibility assumption.  Encoded dimensions come from the file reader,
    never from the untrusted transport header.
    """
    _payload(payload)
    if (not isinstance(metadata, tuple) or len(metadata) != 3
            or any(type(value) is not int for value in metadata)):
        raise ImageFormatError("图像元数据必须为 width、height、format 三个整数的元组")
    width, height, format_id = metadata
    if not 1 <= format_id <= 34:
        raise ImageFormatError(f"图像格式 {format_id} 尚未适配")
    if format_id in ENCODED_FORMATS:
        specification = ENCODED_FORMATS[format_id]
        image = _decode_encoded(payload, specification)
        return DecodedNativeFrame(image, specification.name, specification.suffix,
                                  True, specification.allowed_suffixes, metadata)
    if not 1 <= width <= MAX_RAW_DIMENSION or not 1 <= height <= MAX_RAW_DIMENSION:
        raise ImageFormatError("原始图像宽高必须在 1–16384 之间")
    _pixel_budget(width, height)
    try:
        image_format = QImage.Format(format_id)
    except ValueError as exc:
        raise ImageFormatError(f"当前 Qt 没有原始像素格式 {format_id}") from exc
    probe = QImage(1, 1, image_format)
    depth = probe.depth()
    if probe.isNull() or depth not in {1, 8, 16, 24, 32, 64}:
        raise ImageFormatError(f"当前 Qt 无法处理原始像素格式 {format_id}")
    stride = ((width * depth + 31) // 32) * 4
    expected = stride * height
    if expected > MAX_IMAGE_BYTES:
        raise ImageFormatError("按四字节行对齐计算的原始图像超过 8 MiB")
    if len(payload) != expected:
        raise ImageFormatError(f"原始图像载荷长度不匹配：应为 {expected} 字节，实际 {len(payload)} 字节")
    # Copy while the immutable Python buffer is still alive.  The returned
    # QImage owns its memory and cannot outlive a borrowed bytes pointer.
    image = QImage(payload, width, height, stride, image_format).copy()
    if image.isNull():
        raise ImageFormatError("原始图像复制或分配失败")
    return DecodedNativeFrame(image, f"RAW {image_format.name}", ".png", False,
                              frozenset({".png"}), metadata)
