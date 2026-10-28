"""Bounded ANSI SGR display support; not a native terminal equivalence claim."""

from __future__ import annotations

from qx_stc_isp.gui import TerminalView
from qx_stc_isp.vofa.ansi import AnsiSink


def test_sgr_colors_and_reset():
    segments = AnsiSink().feed("\x1b[31mred\x1b[0m plain")
    assert [text for text, _style in segments] == ["red", " plain"]
    assert segments[0][1].foreground == "#800000"
    assert segments[1][1].foreground is None


def test_split_escape_and_unsupported_sequences_are_removed():
    sink = AnsiSink()
    assert sink.feed("\x1b[3") == []
    assert sink.feed("1mgreen") == [("green", sink.style)]
    assert sink.style.foreground == "#800000"
    cleaned = AnsiSink().feed("\x1b[2Kdone\x1b]0;title\x07!")
    assert "".join(text for text, _style in cleaned) == "done!"
    assert all(style == AnsiSink().style for _text, style in cleaned)


def test_extended_colors_and_attributes():
    segments = AnsiSink().feed("\x1b[38;5;196mX\x1b[1;4mY")
    assert segments[0][1].foreground == "#ff0000"
    assert "196" not in segments[0][0]
    assert segments[1][1].bold and segments[1][1].underline
    truecolor = AnsiSink().feed("\x1b[38;2;1;2;3mZ")
    assert truecolor[0][1].foreground == "#010203"


def test_terminal_view_renders_text_without_escape_codes(qtbot):
    view = TerminalView()
    qtbot.addWidget(view)
    sink = AnsiSink()
    view.append_ansi("\x1b[32mOK\x1b[0m done", sink, "[12:00:00] ")
    assert "[12:00:00] OK done" in view.toPlainText()
    assert "\x1b" not in view.toPlainText()
    view.appendPlainText("plain")
    assert "plain" in view.toPlainText()
