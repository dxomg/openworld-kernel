# openworld-kernel

Size-optimized Linux kernels for [Firecracker](https://firecracker-microvm.github.io/) microVMs, built entirely in GitHub Actions — nothing to compile on your machine.

Both images target a Linux 6.18 LTS kernel from `linux-stable` (`linux-6.18.y`), are built with `-Os`, have zero modules, and ship as standalone `vmlinux` ELF images ready for Firecracker.

## Current builds (6.18.52)

| Arch | Stripped `vmlinux` | Transport | Serial console |
| ---- | ------------------ | --------- | -------------- |
| x86_64 | **15.6 MiB** | virtio-PCI + virtio-MMIO | 16550 (`ttyS0`) |
| arm64 (aarch64) | **10.6 MiB** | virtio-MMIO | PL011 |

Files are published to **GitHub Releases** under tag `kernel-6.18.52-2026-09-14`:

```
vmlinux-<arch>-<version>            # unstripped ELF
vmlinux-<arch>-<version>-stripped   # stripped ELF (use this with Firecracker)
kernel-<arch>-<version>.config      # the exact config the image was built from
SHA256SUMS-<arch>                   # checksums
```

Download with:

```bash
gh release download kernel-6.18.52-2026-09-14 --pattern 'vmlinux-*' -D kernels
# or browser: https://github.com/dxomg/openworld-kernel/releases
```

## Features

Common to both arches:

- **Block & net**: `virtio_blk`, `virtio_net` (with `virtio-mmio`; plus `virtio-pci` on x86_64)
- **Console**: virtio console + serial console (8250 on x86, PL011 on arm64), early printk
- **Balloon**: `virtio_balloon`
- **Networking**: IPv4 + IPv6, `AF_UNIX`, `AF_PACKET`, vsock
- **Filesystems**: ext4, overlayfs, tmpfs, squashfs (x86_64), vfat (arm64), and `9p`
- **Init**: built-in initramfs support (gzip), `devtmpfs`
- **Memory**: swap + zswap (lz4, on by default), hugetlb-free
- **Security**: SELinux + AppArmor (LSM chain `lockdown,yama,loadpin,safesetid,apparmor,selinux,bpf`)
- **Userspace ABI**: io_uring, epoll, eventfd/signalfd/timerfd, membarrier, rseq, futex, POSIX mqueues
- **SMP**: up to 32 (x86) / 64 (arm64) vCPUs
- **Smallness**: `-Os`, no modules, no debug info / BTF

## Deliberately trimmed / disabled

Since this is a size-optimized image, several things a desktop kernel would have are off by default. Most are configurable — see `kernel.config` and re-run `olddefconfig`.

- **Virtual terminals, but no input devices**: `CONFIG_VT` is *enabled* so `/dev/tty1-6` exist for init/getty, but all input device drivers are disabled (no keyboard/mouse).
- **No PMU/tracing**: x86's base `PERF_EVENTS` is force-selected by the arch, but all uncore/RAPL/CSTATE PMU drivers and the `FTRACE`/`TRACING`/`uprobe`/`kprobe` stack are off.
- **Frame-pointer unwinder** instead of ORC (~1.6 MiB of unwind tables saved).
- **No KASLR, no MTRR, no halt-poll idle, no ASPM** (x86) — minor, and not needed in a microVM.
- **No `KALLSYMS`** — oops traces show raw addresses; keep this off for ~small gain or re-enable if you debug crashes.
- **Full CPU speculation mitigations are compiled in** — not trimmed. retpoline, RETHUNK, IBRS/IBPB entry, and every vendor `MITIGATION_*` option (BHI, GDS, RFDS, MDS, TAA, MMIO stale data, L1TF, SRBDS, SSB, ITS, SLS, …) are on, so `/sys/devices/system/cpu/vulnerabilities/*` and the boot log show active mitigations instead of `Vulnerable`. This costs a bit of text size compared to a trimmed build. See the `# Security` notes in `kernel.config`.
- **No IOMMU, no EFI, no USB/HID/DRM/FB/sound, no SCSI/ATA/NVMe** — out of scope for Firecracker.

## Why x86_64 is ~5 MiB bigger than arm64

Not a defect — the arch requires it:

- x86 needs **ACPI** for multi-vCPU discovery (Firecracker exposes MADT/APIC tables). No ACPI = single vCPU.
- x86 needs **PARAVIRT / KVM guest** support and keeps a force-selected `PERF_EVENTS`.
- The x86 arch code itself (IDT, LAPIC/IOAPIC, traps, vendor CPU tables) is heavier than arm64's.

## Automated builds

| Trigger | Effect |
| ------- | ------ |
| Push to `main` | Rebuilds both arches and (re)creates the release for that kernel version |
| `workflow_dispatch` | Manual build; inputs allow a different Linux ref or config |
| Cron (Mon 03:00 UTC) | Weekly rebuild so the images track the latest `linux-6.18.y` patch |

`workflow_dispatch` inputs:

- `kernel_ref` — Linux ref to build (default `linux-6.18.y`; e.g. `linux-6.12.y`, or a tag).
- `config_path` — x86 config to use (default `.github/firecracker/kernel.config`; arm64 always uses `.github/firecracker/kernel.config.arm64`).

The release tag is `kernel-<version>-<YYYY-MM-DD>` and is recreated idempotently on every build, so the newest artifacts always live under the same tag.

The full kernel version is printed in the build log (`make kernelversion`), and each `Report kernel size` step prints the KiB size of every artifact.

## Repository layout

```
.github/
  workflows/build-firecracker-kernel.yml   # build + release pipeline
  firecracker/
    kernel.config                         # x86_64 config fragment (main file)
    kernel.config.arm64                   # aarch64 config fragment
```

The configs are *fragments*, not full `.config` files: the workflow copies the fragment onto `.config` and runs `make olddefconfig` to resolve the rest from Kconfig defaults. That keeps them readable and lets the 6.18 defaults fill in automatically. The exact generated config is published next to each image.

## Using with Firecracker

Point `kernel_image_path` at the stripped image and give a sane `boot_args`. Example for x86_64 with virtio-MMIO:

```json
{
  "kernel_image_path": "vmlinux-x86_64-6.18.52-stripped",
  "boot_args": "console=ttyS0 reboot=k panic=1 pci=off root=/dev/vda rw virtio_mmio.device=4K@0xc0001000:5",
  "vcpu_count": 2,
  "mem_size_mib": 1024
}
```

Notes:

- **virtio-PCI vs virtio-MMIO**: Firecracker's default x86 transport is virtio-PCI; then no `virtio_mmio.device=` entries are needed. If you pass `pci=off` (as above), you must add one `virtio_mmio.device=<size>@<addr>:<irq>` entry **per device**, using distinct, non-overlapping addresses in the reserved hole (e.g. `0x40000000`–`0xeebfffff`) — addresses inside the low RAM region fail with `-EBUSY`.
- **Multi-core**: `vcpu_count` > 1 is supported on both arches (`CONFIG_SMP`). On x86 this relies on ACPI (kept enabled).
- **arm64** always uses virtio-MMIO; give the same `console=`/`root=` style boot args.
- There is no initramfs included; either provide one on kernel cmdline (`initrd=`) or a root filesystem (`root=/dev/vda`).
- `/dev/ttyS0` is the primary console; Alpine/getty on virtual terminals now works because `/dev/tty1-6` exist.

## Building locally (optional)

The pipeline is designed to run in CI, but if you want to poke at configs locally (config-only, quick):

```bash
git clone --depth 1 --branch linux-6.18.y https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git linux
cp .github/firecracker/kernel.config linux/.config
cd linux
make olddefconfig                 # resolve the fragment
# inspect: grep CONFIG_FEATURE .config
```

To do a full build you'll also need `gcc make flex bison bc libssl-dev libelf-dev kmod perl`
(plus `gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu` for arm64) and then `make -j vmlinux`.

## License

Config fragments and workflow are yours to use (kernel itself is GPL-2.0, built from `linux-stable`).