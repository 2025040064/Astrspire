/* Passive Win32 UART API observation for the user-supplied AiCube process.
 * No return value, target memory, serial port or authorization state is changed.
 * A submitted overlapped write is not claimed as a completed TX until confirmed.
 */

const openPorts = new Map();
const pendingIo = new Map();
const MAX_BYTES = 65536;

function report(kind, fields) {
  send(Object.assign({ tag: "aicube-uart", at: new Date().toISOString(), kind }, fields));
}

function captureBytes(address, requested) {
  const count = Math.min(requested, MAX_BYTES);
  try {
    const value = address.readByteArray(count);
    const hex = Array.from(new Uint8Array(value), byte => byte.toString(16).padStart(2, "0")).join("");
    return { hex, requested, captured: count, truncated: requested > count };
  } catch (error) {
    return { hex: null, requested, captured: 0, truncated: true, read_error: String(error) };
  }
}

function readCount(address) {
  try { return address.isNull() ? null : address.readU32(); }
  catch (_) { return null; }
}

function attach(api, handlers) {
  let address = null;
  for (const moduleName of ["KernelBase.dll", "kernel32.dll"]) {
    try {
      address = Process.getModuleByName(moduleName).findExportByName(api);
      if (address !== null) break;
    } catch (_) { /* Try the other forwarding module. */ }
  }
  if (address === null) {
    report("HOOK_UNAVAILABLE", { api });
    return;
  }
  Interceptor.attach(address, handlers);
}

function trackCreate(api, wide) {
  attach(api, {
    onEnter(args) {
      try { this.name = wide ? args[0].readUtf16String() : args[0].readAnsiString(); }
      catch (_) { this.name = ""; }
    },
    onLeave(result) {
      if (!/(?:^|\\)COM[0-9]+$/i.test(this.name || "")) return;
      const handle = result.toString();
      if (handle === "0xffffffff" || handle === "0xffffffffffffffff") {
        report("OPEN_FAILED", { api, port: this.name });
        return;
      }
      openPorts.set(handle, this.name);
      report("OPEN", { api, handle, port: this.name });
    }
  });
}

trackCreate("CreateFileW", true);
trackCreate("CreateFileA", false);

attach("SetCommState", {
  onEnter(args) {
    const port = openPorts.get(args[0].toString());
    if (!port) return;
    try {
      const dcb = args[1];
      report("DCB_REQUEST", {
        port, baudrate: dcb.add(4).readU32(), byte_size: dcb.add(18).readU8(),
        parity: dcb.add(19).readU8(), stop_bits: dcb.add(20).readU8(),
        flags: dcb.add(8).readU32()
      });
    } catch (error) { report("DCB_UNREADABLE", { port, error: String(error) }); }
  }
});

attach("SetCommTimeouts", {
  onEnter(args) {
    const port = openPorts.get(args[0].toString());
    if (!port) return;
    try {
      const p = args[1];
      report("TIMEOUTS_REQUEST", {
        port, read_interval: p.readU32(), read_multiplier: p.add(4).readU32(),
        read_constant: p.add(8).readU32(), write_multiplier: p.add(12).readU32(),
        write_constant: p.add(16).readU32()
      });
    } catch (error) { report("TIMEOUTS_UNREADABLE", { port, error: String(error) }); }
  }
});

attach("WriteFile", {
  onEnter(args) {
    this.port = openPorts.get(args[0].toString());
    if (!this.port) return;
    this.packet = captureBytes(args[1], args[2].toUInt32());
    this.written = args[3];
    this.overlap = args[4];
    this.handle = args[0].toString();
  },
  onLeave(result) {
    if (!this.port) return;
    if (result.toInt32() !== 0) {
      report("TX_COMPLETE", { port: this.port, count: readCount(this.written), ...this.packet });
    } else if (!this.overlap.isNull()) {
      pendingIo.set(this.overlap.toString(), {
        direction: "TX", port: this.port, handle: this.handle, packet: this.packet
      });
      report("TX_PENDING_CANDIDATE", { port: this.port, overlap: this.overlap.toString(), ...this.packet });
    } else {
      report("TX_FAILED", { port: this.port, ...this.packet });
    }
  }
});

attach("ReadFile", {
  onEnter(args) {
    this.port = openPorts.get(args[0].toString());
    if (!this.port) return;
    this.buffer = args[1];
    this.requested = args[2].toUInt32();
    this.count = args[3];
    this.overlap = args[4];
    this.handle = args[0].toString();
  },
  onLeave(result) {
    if (!this.port) return;
    if (result.toInt32() !== 0) {
      const count = readCount(this.count);
      if (count !== null && count > 0)
        report("RX_COMPLETE", { port: this.port, ...captureBytes(this.buffer, count) });
    } else if (!this.overlap.isNull()) {
      pendingIo.set(this.overlap.toString(), {
        direction: "RX", port: this.port, handle: this.handle, buffer: this.buffer
      });
      report("RX_PENDING_CANDIDATE", { port: this.port, overlap: this.overlap.toString(), requested: this.requested });
    }
  }
});

attach("GetOverlappedResult", {
  onEnter(args) {
    this.key = args[1].toString();
    this.count = args[2];
    this.handle = args[0].toString();
  },
  onLeave(result) {
    if (result.toInt32() === 0) return;
    const task = pendingIo.get(this.key);
    if (!task || task.handle !== this.handle) return;
    pendingIo.delete(this.key);
    const count = readCount(this.count);
    if (task.direction === "RX" && count !== null && count > 0)
      report("RX_COMPLETE", { port: task.port, overlap: this.key, ...captureBytes(task.buffer, count) });
    if (task.direction === "TX")
      report("TX_PENDING_COMPLETE", { port: task.port, overlap: this.key, count, ...task.packet });
  }
});

for (const api of ["PurgeComm", "CancelIo", "CancelIoEx", "ClearCommError"]) {
  attach(api, {
    onEnter(args) {
      const port = openPorts.get(args[0].toString());
      if (port) report(api.toUpperCase(), { port, handle: args[0].toString() });
    }
  });
}

attach("CloseHandle", {
  onEnter(args) {
    const handle = args[0].toString();
    const port = openPorts.get(handle);
    if (!port) return;
    openPorts.delete(handle);
    for (const [key, task] of pendingIo) if (task.handle === handle) pendingIo.delete(key);
    report("CLOSE", { port, handle });
  }
});

report("READY", { note: "Passive API hooks installed; unobserved completion APIs remain a capture gap." });
