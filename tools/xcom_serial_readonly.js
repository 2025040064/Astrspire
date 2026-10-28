/* Read-only API observation for the selected XCOM process.
 * Does not replace arguments, return values, target bytes or application state.
 * Only COM7/COM8 handles are recorded; asynchronous RX may remain incomplete.
 */
'use strict';
const handles = new Set();
const pendingReads = new Map();
let eventCount = 0;
const eventLimit = 400;

function emit(kind, fields) {
    if (eventCount++ < eventLimit) send(Object.assign({ kind, at: Date.now() }, fields));
}
function hex(address, length) {
    try {
        const bytes = new Uint8Array(address.readByteArray(Math.min(length, 256)));
        return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join(' ');
    } catch (_) { return null; }
}
function attach(name, callbacks) {
    const address = Module.findGlobalExportByName(name);
    if (address === null) { emit('unavailable', { api: name }); return; }
    Interceptor.attach(address, callbacks);
}
for (const api of ['CreateFileW', 'CreateFileA']) {
    attach(api, {
        onEnter(args) {
            try { this.path = api.endsWith('W') ? args[0].readUtf16String() : args[0].readCString(); }
            catch (_) { this.path = ''; }
            this.selected = /(?:^|\\)COM[78]$/i.test(this.path);
            if (this.selected) this.flags = args[5].toUInt32();
        },
        onLeave(result) {
            if (!this.selected) return;
            if (result.toInt32() !== -1) handles.add(result.toString());
            emit('open', { api, path: this.path, handle: result.toString(), flags: this.flags });
        }
    });
}
attach('SetCommState', {
    onEnter(args) {
        this.selected = handles.has(args[0].toString());
        if (!this.selected) return;
        const value = args[1];
        const flags = value.add(8).readU32();
        this.config = { handle: args[0].toString(), baudrate: value.add(4).readU32(), flags,
            bytesize: value.add(18).readU8(), parity: value.add(19).readU8(), stopbits: value.add(20).readU8(),
            dtr_control: (flags >>> 4) & 3, rts_control: (flags >>> 12) & 3,
            cts_flow: !!(flags & 4), dsr_flow: !!(flags & 8), discard_null: !!(flags & 2048) };
    },
    onLeave(result) { if (this.selected) emit('dcb', Object.assign(this.config, { success: result.toInt32() !== 0 })); }
});
attach('SetCommTimeouts', {
    onEnter(args) {
        this.selected = handles.has(args[0].toString());
        if (this.selected) this.value = [0, 4, 8, 12, 16].map(offset => args[1].add(offset).readU32());
    },
    onLeave(result) { if (this.selected) emit('timeouts', { values: this.value, success: result.toInt32() !== 0 }); }
});
attach('EscapeCommFunction', {
    onEnter(args) {
        this.selected = handles.has(args[0].toString());
        if (this.selected) this.value = args[1].toUInt32();
    },
    onLeave(result) { if (this.selected) emit('line_control', { operation: this.value, success: result.toInt32() !== 0 }); }
});
attach('WriteFile', {
    onEnter(args) {
        this.selected = handles.has(args[0].toString());
        if (!this.selected) return;
        this.amount = args[2].toUInt32();
        this.bytes = hex(args[1], this.amount);
    },
    onLeave(result) { if (this.selected) emit('tx_call', { bytes: this.amount, preview_hex: this.bytes, synchronous_success: result.toInt32() !== 0 }); }
});
attach('ReadFile', {
    onEnter(args) {
        this.selected = handles.has(args[0].toString());
        if (!this.selected) return;
        this.buffer = args[1]; this.requested = args[2].toUInt32(); this.count = args[3]; this.overlap = args[4];
        if (!this.overlap.isNull()) pendingReads.set(this.overlap.toString(), { buffer: this.buffer, requested: this.requested });
    },
    onLeave(result) {
        if (!this.selected || result.toInt32() === 0 || this.count.isNull()) return;
        const length = this.count.readU32();
        if (length) emit('rx_sync', { bytes: length, preview_hex: hex(this.buffer, length) });
        if (!this.overlap.isNull()) pendingReads.delete(this.overlap.toString());
    }
});
attach('GetOverlappedResult', {
    onEnter(args) { this.key = args[1].toString(); this.count = args[2]; },
    onLeave(result) {
        const read = pendingReads.get(this.key);
        if (!read || result.toInt32() === 0 || this.count.isNull()) return;
        const length = this.count.readU32();
        if (length) emit('rx_overlapped', { bytes: length, preview_hex: hex(read.buffer, length) });
        pendingReads.delete(this.key);
    }
});
attach('CloseHandle', {
    onEnter(args) { this.key = args[0].toString(); this.selected = handles.has(this.key); },
    onLeave(result) {
        if (this.selected && result.toInt32() !== 0) {
            handles.delete(this.key);
            pendingReads.clear();
            emit('close', { handle: this.key });
        }
    }
});
emit('ready', { target_ports: ['COM7', 'COM8'], event_limit: eventLimit,
    limitation: 'Asynchronous RX without GetOverlappedResult is not observed; previews stop at 256 bytes' });
