"""Build only: never opens a probe or serial port, and never flashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hal-root", type=Path, default=Path(r"C:\Users\Legion\STM32Cube\Repository\STM32Cube_FW_G4_V1.6.3"))
    parser.add_argument("--gcc", type=Path, default=Path(r"D:\tools\STM32CubeCLT_1.18.0\GNU-tools-for-STM32\bin\arm-none-eabi-gcc.exe"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "firmware/g431_uart"
    output = root / "build-preview/runtime-g431-uart-20261027/firmware"
    output.mkdir(parents=True, exist_ok=True)
    drivers = args.hal_root / "Drivers"
    device = drivers / "CMSIS/Device/ST/STM32G4xx"
    hal = drivers / "STM32G4xx_HAL_Driver"
    names = ("hal", "hal_cortex", "hal_rcc", "hal_rcc_ex", "hal_flash", "hal_flash_ex",
             "hal_gpio", "hal_pwr", "hal_pwr_ex", "hal_dma", "hal_dma_ex", "hal_uart", "hal_uart_ex")
    sources = [source / "main.c", device / "Source/Templates/system_stm32g4xx.c",
               device / "Source/Templates/gcc/startup_stm32g431xx.s"]
    sources += [hal / "Src" / f"stm32g4xx_{name}.c" for name in names]
    for path in [args.gcc, *sources]:
        if not path.is_file():
            raise FileNotFoundError(path)
    command = [str(args.gcc), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-O2", "-g",
               "-Wall", "-Wextra", "-ffunction-sections", "-fdata-sections", "-nostartfiles",
               "--specs=nano.specs", "--specs=nosys.specs", "-DUSE_HAL_DRIVER", "-DSTM32G431xx"]
    for path in (source, hal / "Inc", device / "Include", drivers / "CMSIS/Include"):
        command += ["-I", str(path)]
    elf = output / "g431_uart.elf"
    command += [str(path) for path in sources] + ["-Wl,--gc-sections", "-T", str(root / "firmware/swd_capture/capture.ld"),
               "-o", str(elf), "-lc", "-lgcc"]
    built = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=120)
    (output / "build.log").write_text(built.stdout + built.stderr, encoding="utf-8")
    if built.returncode:
        raise RuntimeError(built.stderr)
    for kind, suffix in (("binary", "bin"), ("ihex", "hex")):
        subprocess.run([str(args.gcc.parent / "arm-none-eabi-objcopy.exe"), "-O", kind, str(elf),
                        str(output / f"g431_uart.{suffix}")], check=True)
    binary = output / "g431_uart.bin"
    manifest = dict(chip="STM32G431RBT6", device_id="0x468", interface="USART1",
        tx="PA9", rx="PA10", baudrate=115200, format="8N1", decoder="JustFloat", channels=16,
        period_ms=10, firmware_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        bytes=binary.stat().st_size, hardware_verified=False,
        hello="ASTRSPIRE:G431_UART_V1,115200,16,10", streaming_at_startup=False)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
