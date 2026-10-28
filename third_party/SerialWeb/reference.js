      function newlineText(mode) {
        if (mode === 'r') return '\r';
        if (mode === 'n') return '\n';
        return '\r\n';
      }

      function normalizeTextLineEndings(text, newlineMode) {
        return String(text ?? '').replace(/\r\n|\r|\n/g, newlineText(newlineMode));
      }

      function normalizeEncodingLabel(encoding) {
        const enc = String(encoding || 'utf-8').toLowerCase();
        if (enc === 'utf8') return 'utf-8';
        if (enc === 'iso-8859-1' || enc === 'iso8859-1') return 'latin1';
        if (enc === 'utf16le' || enc === 'utf-16') return 'utf-16le';
        return enc;
      }

      function decodeBytes(bytes, encoding) {
        const enc = normalizeEncodingLabel(encoding);
        const source = bytes instanceof Uint8Array ? bytes : Uint8Array.from(bytes || []);
        // latin1 / ascii 自行解码：TextDecoder('latin1'|'ascii') 等价于 windows-1252，
        // 与下方自定义编码器在 0x80–0x9F 段不一致，这里统一为严格语义
        if (enc === 'latin1') {
          let out = '';
          for (let index = 0; index < source.length; index += 1) {
            out += String.fromCharCode(source[index]);
          }
          return out;
        }
        if (enc === 'ascii') {
          let out = '';
          for (let index = 0; index < source.length; index += 1) {
            const byte = source[index];
            out += byte <= 0x7f ? String.fromCharCode(byte) : '\uFFFD';
          }
          return out;
        }
        try {
          return new TextDecoder(enc).decode(source);
        } catch (error) {
          return new TextDecoder('utf-8').decode(source);
        }
      }

      function encodeText(text, encoding) {
        const value = String(text || '');
        const enc = normalizeEncodingLabel(encoding);
        if (enc === 'ascii') {
          // 严格 US-ASCII：超出 7 位的字符替换为 '?'，不再静默掩码成错误字节
          return Uint8Array.from(Array.from(value).map((char) => {
            const code = char.codePointAt(0) || 0;
            return code <= 0x7f ? code : 0x3f;
          }));
        }
        if (enc === 'latin1') {
          // ISO-8859-1：超出 0xFF 的字符替换为 '?'，与解码方向保持一致
          return Uint8Array.from(Array.from(value).map((char) => {
            const code = char.codePointAt(0) || 0;
            return code <= 0xff ? code : 0x3f;
          }));
        }
        if (enc === 'utf-16le') {
          const buffer = new Uint8Array(value.length * 2);
          for (let index = 0; index < value.length; index += 1) {
            const code = value.charCodeAt(index);
            buffer[index * 2] = code & 0xff;
            buffer[index * 2 + 1] = code >> 8;
          }
          return buffer;
        }
        return new TextEncoder().encode(value);
      }
      function getIncompleteRxTailLength(bytes, encoding) {
        const length = bytes?.length || 0;
        if (!length) return 0;
        const enc = String(encoding || 'utf-8').toLowerCase();
        if (enc === 'utf-16le' || enc === 'utf-16' || enc === 'utf16le') {
          return length % 2 === 1 ? 1 : 0;
        }
        if (enc !== 'utf-8' && enc !== 'utf8') return 0;
        let continuations = 0;
        let index = length - 1;
        while (index >= 0 && bytes[index] >= 0x80 && bytes[index] <= 0xbf) {
          continuations += 1;
          index -= 1;
        }
        if (index < 0) return 0;
        const lead = bytes[index];
        let expected = 0;
        if (lead >= 0xc2 && lead <= 0xdf) expected = 2;
        else if (lead >= 0xe0 && lead <= 0xef) expected = 3;
        else if (lead >= 0xf0 && lead <= 0xf4) expected = 4;
        else return 0;
        const available = continuations + 1;
        return available < expected ? available : 0;
      }

      function isUtf16LeEncoding(encoding) {
        return normalizeEncodingLabel(encoding) === 'utf-16le';
      }

      // 编码感知地识别行终止符：UTF-16LE 按 2 字节码元判断，返回 { endIndex, loneCR } 或 null
      function getRxLineTerminator(source, index, encoding) {
        if (isUtf16LeEncoding(encoding)) {
          if (index + 1 >= source.length) return null;
          const codeUnit = source[index] | (source[index + 1] << 8);
          if (codeUnit === 0x000a) return { endIndex: index + 2, loneCR: false };
          if (codeUnit === 0x000d) {
            const hasLf = index + 3 < source.length && source[index + 2] === 0x0a && source[index + 3] === 0x00;
            return { endIndex: hasLf ? index + 4 : index + 2, loneCR: !hasLf };
          }
          return null;
        }
        const value = source[index];
        if (value === 0x0d) {
          const hasLf = index + 1 < source.length && source[index + 1] === 0x0a;
          return { endIndex: hasLf ? index + 2 : index + 1, loneCR: !hasLf };
        }
        if (value === 0x0a) return { endIndex: index + 1, loneCR: false };
        return null;
      }

      // 上一 chunk 以孤立 CR 收尾时，下一 chunk 开头需跳过的 LF 字节数
      function getRxLeadingLfSkip(source, encoding) {
        if (isUtf16LeEncoding(encoding)) {
          return (source.length >= 2 && source[0] === 0x0a && source[1] === 0x00) ? 2 : 0;
        }
        return source[0] === 0x0a ? 1 : 0;
      }

      // 将一段接收字节按行切分（实时与回放两条路径共用，避免逻辑漂移）
      function segmentRxSource(source, encoding, carry) {
        const step = isUtf16LeEncoding(encoding) ? 2 : 1;
        const lines = [];
        let tail = null;
        let segmentStart = 0;
        let index = 0;
        if (carry.pendingCR) {
          carry.pendingCR = false;
          const skip = getRxLeadingLfSkip(source, encoding);
          if (skip) {
            index = skip;
            segmentStart = skip;
          }
        }
        while (index < source.length) {
          const terminator = getRxLineTerminator(source, index, encoding);
          if (terminator) {
            const segmentBytes = source.slice(segmentStart, terminator.endIndex);
            if (segmentBytes.length) lines.push(segmentBytes);
            carry.pendingCR = terminator.loneCR && terminator.endIndex === source.length;
            index = terminator.endIndex;
            segmentStart = terminator.endIndex;
            continue;
          }
          index += step;
        }
        if (segmentStart < source.length) {
          tail = source.slice(segmentStart);
        }
        return { lines, tail };
      }

      function queueRxBytes(bytes, timestamp = nowPreciseMs(), metadata = {}) {
        if (!bytes?.length) return;
        const rawChunk = recordRawChunk('rx', bytes, timestamp, metadata);
        const received = bytes instanceof Uint8Array ? bytes : Uint8Array.from(bytes || []);
