#!/usr/bin/env python3
"""Fail CI if Kconfig silently drops the hosting profile's key settings."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("arch", choices=("x86_64", "arm64"))
    args = parser.parse_args()
    values = {}
    for line in args.config.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key.removeprefix("CONFIG_")] = value

    required = {
        key: "y" for key in (
            "CC_OPTIMIZE_FOR_SIZE", "SMP", "VIRTIO_BALLOON", "VIRTIO_MMIO",
            "VIRTIO_BLK", "VIRTIO_NET", "VIRTIO_VSOCKETS", "PAGE_REPORTING",
            "BALLOON_COMPACTION", "COMPACTION", "MIGRATION", "VM_EVENT_COUNTERS",
            "MEMORY_HOTPLUG", "MEMORY_HOTREMOVE",
            "LRU_GEN", "LRU_GEN_ENABLED", "SLUB_TINY", "SLAB_MERGE_DEFAULT",
            "SWAP", "ZSWAP", "ZSWAP_DEFAULT_ON", "ZSWAP_SHRINKER_DEFAULT_ON",
            "ZSWAP_COMPRESSOR_DEFAULT_LZ4", "SYSCTL", "PROC_FS", "SYSFS",
            "EXT4_FS", "DEVTMPFS", "BINFMT_ELF", "RD_GZIP", "UNIX98_PTYS",
            "TTY", "VT", "VT_CONSOLE", "INPUT",
            "SECURITY_APPARMOR", "SECURITY_SELINUX",
        )
    }
    required.update(LOG_BUF_SHIFT="14", LOG_CPU_MAX_BUF_SHIFT="0")
    if args.arch == "x86_64":
        required.update(ACPI="y", VIRTIO_PCI="y", SERIAL_8250_CONSOLE="y",
                        SERIAL_8250_NR_UARTS="1", SERIAL_8250_RUNTIME_UARTS="1",
                        CPU_MITIGATIONS="y", MITIGATION_SLS="y")
    else:
        required.update(ARM64_4K_PAGES="y", SERIAL_AMBA_PL011_CONSOLE="y")

    disabled = (
        "MODULES", "IKCONFIG", "SERIO", "NET_9P", "9P_FS",
        "NUMA", "IDLE_PAGE_TRACKING", "SLUB_CPU_PARTIAL",
        "TRANSPARENT_HUGEPAGE", "HUGETLBFS", "DEBUG_INFO", "DEBUG_INFO_BTF",
        "DEBUG_MISC", "RCU_TRACE", "RD_BZIP2", "RD_LZMA", "RD_XZ", "RD_LZO",
        "RD_LZ4", "RD_ZSTD",
    )
    required.update({key: "n" for key in disabled})
    errors = [f"CONFIG_{key}: expected {expected}, got {values.get(key, 'n')}"
              for key, expected in required.items()
              if values.get(key, "n") != expected]
    errors.extend(f"CONFIG_{key}: unexpected module"
                  for key, value in values.items() if value == "m")
    if errors:
        raise SystemExit("Resolved config violates the hosting profile:\n" + "\n".join(errors))
    print(f"{args.arch}: {len(required)} hosting config checks passed")


if __name__ == "__main__":
    main()
