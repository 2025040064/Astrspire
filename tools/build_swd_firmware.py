"""Build an independent HAL experiment and derive SRAM address from ELF.

Vendor source trees are read-only inputs, not copied into user projects.
This command builds files only; it never attaches to a probe or flashes.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess

from qx_stc_isp.vofa.swd import SwdManifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("stm32f1x", "stm32g4x"), required=True)
    parser.add_argument("--hal-root", type=Path)
    parser.add_argument("--gcc", type=Path, default=Path(r"D:\tools\STM32CubeCLT_1.18.0\GNU-tools-for-STM32\bin\arm-none-eabi-gcc.exe"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "firmware/swd_capture"
    f1 = args.target == "stm32f1x"
    sdk = args.hal_root or (Path(r"C:\Users\Legion\STM32Cube\Repository\STM32Cube_FW_F1_V1.8.7") if f1 else Path(r"C:\Users\Legion\STM32Cube\Repository\STM32Cube_FW_G4_V1.6.3"))
    family = "STM32F1xx" if f1 else "STM32G4xx"
    low = family.lower()
    drivers = sdk / "Drivers"
    device = drivers / f"CMSIS/Device/ST/{family}"
    hal = drivers / f"{family}_HAL_Driver"
    output = root / "build-preview/runtime-v060-dev3/firmware" / args.target
    output.mkdir(parents=True, exist_ok=True)
    elf, binary, hexfile = (output / f"capture.{suffix}" for suffix in ("elf", "bin", "hex"))
    startup = device / "Source/Templates/gcc" / ("startup_stm32f103xb.s" if f1 else "startup_stm32g431xx.s")
    sources = [source / "main.c", device / f"Source/Templates/system_{low}.c", startup]
    for name in ("hal", "hal_cortex", "hal_rcc", "hal_rcc_ex", "hal_flash", "hal_flash_ex", "hal_gpio", "hal_pwr"):
        sources.append(hal / "Src" / f"{low}_{name}.c")
    if not f1:
        sources.append(hal / "Src" / f"{low}_hal_pwr_ex.c")
    if any(not path.is_file() for path in sources):
        raise FileNotFoundError("Missing HAL input: " + ", ".join(str(path) for path in sources if not path.is_file()))
    includes = [source, hal / "Inc", device / "Include", drivers / "CMSIS/Include"]
    command = [str(args.gcc), "-mcpu=" + ("cortex-m3" if f1 else "cortex-m4"), "-mthumb", "-mfloat-abi=soft",
               "-O2", "-ffunction-sections", "-fdata-sections", "-nostartfiles", "--specs=nano.specs", "--specs=nosys.specs",
               "-DUSE_HAL_DRIVER", "-D" + ("STM32F103xB" if f1 else "STM32G431xx")]
    for path in includes:
        command += ["-I", str(path)]
    command += [str(path) for path in sources]
    command += ["-Wl,--gc-sections", "-T", str(source / "capture.ld"), "-o", str(elf), "-lc", "-lgcc"]
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=120)
    (output / "build.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr)
    bin_dir = args.gcc.parent
    for kind, destination in (("binary", binary), ("ihex", hexfile)):
        subprocess.run([str(bin_dir / "arm-none-eabi-objcopy.exe"), "-O", kind, str(elf), str(destination)], check=True)
    symbols = subprocess.check_output([str(bin_dir / "arm-none-eabi-nm.exe"), "-S", str(elf)], text=True)
    address = size = None
    for line in symbols.splitlines():
        fields = line.split()
        if fields and fields[-1] == "astrspire_capture":
            address, size = int(fields[0], 16), int(fields[1], 16)
    if size != 4896:
        raise ValueError("ELF capture layout differs from the reader")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    manifest = SwdManifest(args.target, address, digest)
    (output / "capture.json").write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")
    print(json.dumps({"target": args.target, "bytes": binary.stat().st_size, "address": hex(address), "sha256": digest,
                      "manifest": str(output / "capture.json")}, indent=2))


if __name__ == "__main__":
    main()
