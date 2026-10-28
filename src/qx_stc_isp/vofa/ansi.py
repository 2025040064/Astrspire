"""Bounded ANSI SGR handling for the terminal display.

This is a practical subset of common terminal styling, not a claim of
byte-for-byte equivalence with the native TextBuffer.  Unsupported control
sequences are removed so they can never be rendered as visible text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

_ESCAPE = "\x1b"
_MAX_PENDING = 64

_XTERM = (
    "#000000", "#800000", "#008000", "#808000", "#000080", "#800080", "#008080", "#c0c0c0",
    "#808080", "#ff0000", "#00ff00", "#ffff00", "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
)


@dataclass(frozen=True, slots=True)
class AnsiStyle:
    foreground: str | None = None
    background: str | None = None
    bold: bool = False
    underline: bool = False
    inverse: bool = False


def _xterm_color(index: int) -> str | None:
    if 0 <= index <= 15:
        return _XTERM[index]
    if 16 <= index <= 231:
        index -= 16
        levels = (0, 95, 135, 175, 215, 255)
        red, green, blue = levels[index // 36], levels[index // 6 % 6], levels[index % 6]
        return f"#{red:02x}{green:02x}{blue:02x}"
    if 232 <= index <= 255:
        level = 8 + 10 * (index - 232)
        return f"#{level:02x}{level:02x}{level:02x}"
    return None


def _extended_color(parts: list[int]) -> str | None:
    if len(parts) >= 1 and parts[0] == 5 and len(parts) >= 2:
        return _xterm_color(parts[1])
    if len(parts) >= 4 and parts[0] == 2:
        red, green, blue = (max(0, min(255, value)) for value in parts[1:4])
        return f"#{red:02x}{green:02x}{blue:02x}"
    return None


def _apply(style: AnsiStyle, parameters: list[int]) -> AnsiStyle:
    index = 0
    while index < len(parameters):
        code = parameters[index]
        if code == 0:
            style = AnsiStyle()
        elif code == 1:
            style = replace(style, bold=True)
        elif code == 22:
            style = replace(style, bold=False)
        elif code == 4:
            style = replace(style, underline=True)
        elif code == 24:
            style = replace(style, underline=False)
        elif code == 7:
            style = replace(style, inverse=True)
        elif code == 27:
            style = replace(style, inverse=False)
        elif 30 <= code <= 37:
            style = replace(style, foreground=_XTERM[code - 30])
        elif 90 <= code <= 97:
            style = replace(style, foreground=_XTERM[code - 90 + 8])
        elif code == 39:
            style = replace(style, foreground=None)
        elif 40 <= code <= 47:
            style = replace(style, background=_XTERM[code - 40])
        elif 100 <= code <= 107:
            style = replace(style, background=_XTERM[code - 100 + 8])
        elif code == 49:
            style = replace(style, background=None)
        elif code in (38, 48):
            color = _extended_color(parameters[index + 1:])
            if color is not None:
                if code == 38:
                    style = replace(style, foreground=color)
                else:
                    style = replace(style, background=color)
            break
        index += 1
    return style


class AnsiSink:
    """Incrementally parse text and return ``(text, style)`` segments."""

    def __init__(self) -> None:
        self.style = AnsiStyle()
        self._pending = ""
        self._discarding_osc = False

    def reset(self) -> None:
        self.style = AnsiStyle()
        self._pending = ""
        self._discarding_osc = False

    def feed(self, text: str) -> list[tuple[str, AnsiStyle]]:
        if not isinstance(text, str):
            raise TypeError("ANSI 输入必须为文本")
        data = self._pending + text
        self._pending = ""
        segments: list[tuple[str, AnsiStyle]] = []
        plain: list[str] = []

        def flush() -> None:
            if plain:
                segments.append(("".join(plain), self.style))
                plain.clear()

        index = 0
        while index < len(data):
            character = data[index]
            if self._discarding_osc:
                end = data.find("\x07", index)
                stop = data.find(_ESCAPE + "\\", index)
                candidates = [(position, width) for position, width in ((end, 1), (stop, 2)) if position >= 0]
                if not candidates:
                    # An OSC split across chunks is kept only up to a bound.
                    tail = data[index:]
                    if len(tail) < _MAX_PENDING:
                        self._pending = tail
                    return segments
                position, width = min(candidates)
                index = position + width
                self._discarding_osc = False
                continue
            if character != _ESCAPE:
                plain.append(character)
                index += 1
                continue
            flush()
            if index + 1 >= len(data):
                self._pending = data[index:]
                break
            marker = data[index + 1]
            if marker == "[":
                final = next((position for position in range(index + 2, len(data))
                              if 0x40 <= ord(data[position]) <= 0x7E), None)
                if final is None:
                    tail = data[index:]
                    if len(tail) < _MAX_PENDING:
                        self._pending = tail
                    break
                if data[final] == "m":
                    body = data[index + 2:final]
                    parts = []
                    for field in body.split(";"):
                        try:
                            parts.append(int(field) if field else 0)
                        except ValueError:
                            parts = []
                            break
                    self.style = _apply(self.style, parts or [0])
                index = final + 1
                continue
            if marker == "]":
                index += 2
                self._discarding_osc = True
                continue
            index += 2
        flush()
        return segments
