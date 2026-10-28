"""SerialWeb encoding helpers adapted for the native desktop transport.

SPDX-License-Identifier: AGPL-3.0-only
Derived from Conductance-lab/SerialWeb, commit
ff84c5d43dde6b6c49cddc1b7b01f9c55e3ece1a, index.html.
Copyright: Conductance-lab. See third_party/SerialWeb/NOTICE.md and LICENSE.
Python adaptation: Astrspire contributors, 2026-10-26.
"""

import codecs
import re


def normalize_encoding_label(encoding: str) -> str:
    value = encoding.lower()
    value = {"utf8": "utf-8", "iso-8859-1": "latin-1", "iso8859-1": "latin-1",
             "latin1": "latin-1", "utf16le": "utf-16le", "utf-16": "utf-16le"}.get(value, value)
    if value not in ("utf-8", "ascii", "latin-1", "utf-16le", "gbk"):
        raise ValueError("不支持的文本编码")
    return value


def normalize_text_line_endings(text: str, ending: str) -> str:
    return re.sub(r"\r\n|\r|\n", lambda _match: ending, text)


def encode_text(text: str, encoding: str) -> bytes:
    """Port of encodeText; the desktop send adapter rejects lossy text first."""
    return text.encode(normalize_encoding_label(encoding), errors="replace")


class SerialWebTextDecoder:
    """Keep incomplete multibyte sequences across reads, as queueRxBytes does.

    Python's incremental codec also keeps UTF-16 surrogate pairs intact.
    ASCII is US-ASCII rather than the browser's windows-1252 alias.
    """

    def __init__(self, encoding="utf-8"):
        self.encoding = normalize_encoding_label(encoding)
        self._decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")

    def decode(self, chunk: bytes, final=False) -> str:
        return self._decoder.decode(chunk, final=final)

    def reset(self):
        self._decoder.reset()


class SerialWebLineFramer:
    """Byte-stream port of segmentRxSource's CR/LF carry for UTF-8 lines.

    Original bytes are recorded by the transport independently; a LF after a
    CR at the previous read boundary is not a second application frame.
    """

    def __init__(self, limit=16384):
        self.limit = limit
        self.reset()

    def reset(self):
        self._pending_cr = False
        self._buffer = bytearray()

    def feed(self, chunk):
        lines = []
        index = 0
        while index < len(chunk):
            value = chunk[index]
            index += 1
            if self._pending_cr:
                self._pending_cr = False
                if value == 10:
                    continue
            self._buffer.append(value)
            if value in (10, 13) or len(self._buffer) > self.limit:
                if value == 13 and index < len(chunk) and chunk[index] == 10:
                    self._buffer.append(10)
                    index += 1
                    value = 10
                lines.append(bytes(self._buffer))
                self._buffer.clear()
                self._pending_cr = value == 13
        return lines
