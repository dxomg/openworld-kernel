# openworld-kernel

Size-optimized Linux kernels for [Firecracker](https://firecracker-microvm.github.io/) microVMs, built entirely in GitHub Actions — nothing to compile on your machine.

Both images target Linux 6.18 LTS from `linux-stable` (`linux-6.18.y`), use
`-Os`, and have zero modules. The default profile targets **stock Firecracker**.

## Validated baseline (Linux 6.18.57, before memory hotplug/VT restoration)

Local GCC 14.2 / binutils 2.44 build measurements (not a published release):

These measurements and smoke tests predate re-enabling memory hotplug and virtual terminals.
CI will report updated image sizes for the current configuration.

| Architecture | Deployment artifact | File size |
| --- | --- | --- |
| x86_64 | fully stripped `vmlinux` ELF | **13.93 MiB** |
| arm64 | raw `Image` | **6.88 MiB** |

Both architectures compiled and passed config/packaging checks. An x86_64
smoke test on stock Firecracker v1.17.0 with 2 vCPUs and 256 MiB RAM verified:

- order-0 reporting; host RSS fell from 89,328 to 30,432 KiB after freeing a
  touched 64 MiB allocation (sampled three seconds later);
- inflation to 128 MiB in approximately 0.1 seconds;
- a subsequent 180 MiB allocation completed with OOM deflation and no OOM kill;
- explicit deflation back to zero.

These are small synthetic checks, not application throughput/p99 benchmarks.
The arm64 image was cross-built and header-checked, not boot-tested here.

## Previous release sizes (6.18.52)

These are historical measurements, before the current trims and full symbol
stripping. CI reports new file sizes and ELF load-segment sizes on each build;
file size is not the same as the guest's runtime RAM footprint.

| Arch | Stripped `vmlinux` | Transport | Serial console |
| ---- | ------------------ | --------- | -------------- |
| x86_64 | **15.6 MiB** | virtio-PCI + virtio-MMIO | 16550 (`ttyS0`) |
| arm64 (aarch64) | **10.6 MiB** | virtio-MMIO | PL011 |

That historical release is tagged `kernel-6.18.52-2026-09-14`.
New builds publish these files to **GitHub Releases**:

```
vmlinux-<arch>-<version>            # unstripped ELF
vmlinux-<arch>-<version>-stripped   # fully stripped ELF (boot this on x86_64)
Image-arm64-<version>              # raw ARM64 boot image (new builds)
kernel-<arch>-<version>.config      # the exact config the image was built from
SHA256SUMS-<arch>                   # checksums
```

Download a new build with:

```bash
gh release download <release-tag> --pattern 'vmlinux-*' --pattern 'Image-*' -D kernels
# or browser: https://github.com/dxomg/openworld-kernel/releases
```

## Features

Common to both arches:

- **Block & net**: `virtio_blk`, `virtio_net` (with `virtio-mmio`; plus `virtio-pci` on x86_64)
- **Console**: virtual terminals (`/dev/tty1-6`), virtio console + serial console
  (8250 on x86, PL011 on arm64), early printk
- **Balloon**: stock-compatible **256-PFN / 1 MiB descriptors**, sustained
  direct reclaim, immediate submission of partial batches, 200 ms backoff on
  zero progress, and up to **128 MiB OOM deflation** across batches.
  Emergency reserves remain protected; cache reclaim uses the normal VM path.
- **Free-page reporting**: actual **order-0 / 100 ms** defaults, reclaiming
  fragmented free backing when the host enables reporting. Supported by stock
  Firecracker v1.17.0; no fork required. See [host setup](#ballooning-on-stock-firecracker).
- **Density defaults**: quartered minimum-free curve, `vfs_cache_pressure=1000`,
  dirty ratios 1/2. Tunable through guest sysctls; favor density over cache
  retention and write throughput. See `.github/firecracker/patches/`.
- **Networking**: IPv4 + IPv6, `AF_UNIX`, `AF_PACKET`, vsock
- **Filesystems**: ext4, overlayfs, tmpfs, squashfs (x86_64), vfat (arm64)
- **Init**: gzip or uncompressed initramfs support, `devtmpfs`
- **Memory**: swap + zswap (LZ4 and shrinker on by default; needs backing swap),
  THP/hugetlb off, MGLRU on by default, tiny/merged slabs, fixed 16 KiB log
  buffer including on SMP guests, no NUMA; memory hot-add/hot-remove enabled
  for VMMs that provide the required hotplug mechanism
- **Security**: SELinux + AppArmor (LSM chain `lockdown,yama,loadpin,safesetid,apparmor,selinux,bpf`)
- **Userspace ABI**: io_uring, epoll, eventfd/signalfd/timerfd, membarrier, rseq, futex, POSIX mqueues
- **SMP**: up to 32 (x86) / 64 (arm64) vCPUs
- **Smallness**: `-Os`, no modules, no debug info / BTF, no embedded config,
  no page-idle tracking or unused initramfs decompressors; fully stripped
  x86 deployment ELF with unchanged load segments; raw boot `Image` for arm64

## Deliberately trimmed / disabled

Since this is a size-optimized image, several things a desktop kernel would have are off by default. Most are configurable — see `kernel.config` and re-run `olddefconfig`.

- **No physical input devices or serio**: the input core is enabled for virtual
  terminals. Init/getty can use `/dev/tty1-6`; interactive Firecracker console
  access uses `ttyS0` (x86) or `ttyAMA0` (arm64).
- **No 9p**: stock Firecracker does not expose a virtio-9p device.
- **No PMU/tracing**: x86's base `PERF_EVENTS` is force-selected by the arch, but all uncore/RAPL/CSTATE PMU drivers and the `FTRACE`/`TRACING`/`uprobe`/`kprobe` stack are off.
- **Frame-pointer unwinder** instead of ORC (~1.6 MiB of unwind tables saved).
- **No KASLR, no MTRR, no halt-poll idle, no ASPM** (x86) — minor, and not needed in a microVM.
- **No `KALLSYMS`** — oops traces show raw addresses; keep this off for ~small gain or re-enable if you debug crashes.
- **CPU speculation mitigations are compiled in** — including retpoline,
  RETHUNK, IBRS/IBPB entry and vendor `MITIGATION_*` defaults, with SLS
  explicitly enabled. Runtime mitigation status also depends on the host CPU,
  exposed features and boot arguments; check the guest vulnerability files.
- **No IOMMU, no EFI, no USB/HID/DRM/FB/sound, no SCSI/ATA/NVMe/MMC/MTD** — out of scope for Firecracker.
- **No physical NICs, no wireless/Bluetooth** (`ETHERNET`, `WLAN`/`WIRELESS`, `BT` off) — the guest sees `virtio_net` only.
- **No laptop/platform or host-side virt drivers** (`X86_PLATFORM_DEVICES`/`ARM64_PLATFORM_DEVICES`, `SURFACE_PLATFORMS`, `VIRTUALIZATION`, `VHOST`, `VDPA` off) — guest keeps `KVM_GUEST` only.

## Why the architectures differ in size

The historical release had a roughly 5 MiB ELF-size gap:

- x86 needs **ACPI** for multi-vCPU discovery (Firecracker exposes MADT/APIC tables). No ACPI = single vCPU.
- x86 includes **PARAVIRT / KVM guest** support and a force-selected `PERF_EVENTS`.
- The x86 arch code itself (IDT, LAPIC/IOAPIC, traps, vendor CPU tables) is heavier than arm64's.

## Automated builds

| Trigger | Effect |
| ------- | ------ |
| Push to `main` | Rebuilds both arches and (re)creates the release for that kernel version |
| `workflow_dispatch` | Manual build; inputs allow a different Linux ref or config |
| Cron (Mon 03:00 UTC) | Weekly rebuild so the images track the latest `linux-6.18.y` patch |

`workflow_dispatch` inputs (custom configs must pass the hosting-profile checks):

- `kernel_ref` — Linux ref to build (default `linux-6.18.y`); other versions
  must accept the patch series and pass the config checks.
- `config_path` — x86 config to use (default `.github/firecracker/kernel.config`; arm64 always uses `.github/firecracker/kernel.config.arm64`).

The release tag is `kernel-<version>-<YYYY-MM-DD>` and is recreated idempotently on every build, so the newest artifacts always live under the same tag.

The full kernel version is printed in the build log (`make kernelversion`).
CI checks the **resolved** config so silently ignored Kconfig settings cannot
disable required balloon functionality. Packaging uses `strip --strip-all`,
verifies identical ELF entry points and load segments, and reports both disk
size and loadable memory size. Unstripped images remain available for debugging.

## Repository layout

```
.github/
  workflows/build-firecracker-kernel.yml   # build + release pipeline
  firecracker/
    kernel.config                         # x86_64 config fragment (main file)
    kernel.config.arm64                   # aarch64 config fragment
    check-config.py                       # resolved hosting-profile checks
    check-image.py                        # stripping/load-segment checks
    patches/                              # *.patch applied to linux.git pre-build
```

The configs are *fragments*, not full `.config` files: the workflow copies the fragment onto `.config` and runs `make olddefconfig` to resolve the rest from Kconfig defaults. That keeps them readable and lets the 6.18 defaults fill in automatically. The exact generated config is published next to each image.

## Using with Firecracker

Point `kernel_image_path` at the stripped ELF on **x86_64**, or
`Image-arm64-<version>` on **arm64** (stock Firecracker's arm64 loader does
not accept `vmlinux` ELF). Example x86 boot-source API
body (configure vCPUs/RAM separately through `/machine-config`):

```json
{
  "kernel_image_path": "vmlinux-x86_64-<version>-stripped",
  "boot_args": "console=ttyS0 reboot=k panic=1 root=/dev/vda rw"
}
```

Notes:

- **virtio-PCI and virtio-MMIO**: x86 includes both transports. Let Firecracker
  supply device discovery information; do not hard-code MMIO addresses from a
  different VMM configuration.
- **Multi-core**: `vcpu_count` > 1 is supported on both arches (`CONFIG_SMP`). On x86 this relies on ACPI (kept enabled).
- **arm64** always uses virtio-MMIO; give the same `console=`/`root=` style boot args.
- There is no initramfs included; supply `initrd_path` in the boot-source API
  or a root drive (`root=/dev/vda`). Initramfs must be gzip or uncompressed.
- `/dev/ttyS0` is the x86 console; `/dev/ttyAMA0` is the arm64 console.

## Ballooning on stock Firecracker

For **Firecracker v1.17.0**, install this device **before starting the VM**:

```bash
curl --unix-socket "$FC_SOCKET" -X PUT http://localhost/balloon \
  -H 'Content-Type: application/json' \
  -d '{"amount_mib":0,"deflate_on_oom":true,"stats_polling_interval_s":1,"free_page_reporting":true,"free_page_hinting":false}'
```

Free-page reporting reclaims already-free guest backing automatically, even
with `amount_mib=0`. It does not shrink the guest's capacity or reclaim its
live cache. Hinting is a separate host-triggered developer-preview feature;
it is not needed for continuous reporting. For older Firecracker releases
that lack these fields, omit both and use traditional balloon targets.

To reclaim cache as well, a **host controller** must poll
`GET /balloon/statistics` and issue `PATCH /balloon` with `amount_mib`.
Starting policy, in MiB:

```text
available_mib = floor(available_memory_bytes / 1048576)
candidate = clamp(actual_mib + available_mib - reserve_mib,
                  0, boot_ram_mib - minimum_guest_mib)
```

The target is the **total balloon size**, not the increment. Use `actual_mib`,
not `boot_ram - available`, and wait for fresh statistics and convergence
before another inflation step. Start with `reserve_mib=max(32, 5% of boot RAM)`
and a measured minimum guest budget; reduce the reserve only after burst
tests. Limit inflation steps (for example, 16 MiB per fresh sample), use
hysteresis, and deflate faster when available memory falls or major faults,
swap-ins or allocation stalls rise. After OOM deflation, lower the target
and pause inflation so the controller does not take the memory straight back.

`deflate_on_oom` provides up to 128 MiB of emergency relief from the existing
balloon, not immunity from OOMs. Guest zswap needs active backing swap; without
swap, anonymous working sets cannot be reclaimed this way. Host RSS also
contains VMM overhead and guest kernel memory, so it cannot match only the
application's reported usage.

To favor fewer host THP splits/virtqueue operations over finest-grained
reclaim, boot with `page_reporting.page_reporting_order=9` (2 MiB). The default
is order 0 (4 KiB); reporting delay is a scheduling interval, not an RSS deadline.

### Would a custom Firecracker reclaim more?

**Use stock v1.17.0 first:** it already supports free-page reporting. A custom
VMM and matching kernel could increase the 256-PFN descriptor limit to reduce
round trips, but that primarily changes convergence speed, not how much live
memory is reclaimable. There is no negotiated maximum for this private
extension: larger kernel descriptors must never be sent to a stock VMM.
Workload-aware host targeting is the main remaining lever for density.

References: [v1.17.0 ballooning](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/ballooning.md),
[descriptor limit](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/devices/virtio/balloon/mod.rs).

## Building locally (optional)

The pipeline is designed to run in CI, but if you want to poke at configs locally (config-only, quick):

```bash
git clone --depth 1 --branch linux-6.18.y https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git linux
cp .github/firecracker/kernel.config linux/.config
cd linux
make olddefconfig                 # resolve the fragment
python3 ../.github/firecracker/check-config.py .config x86_64
```

Apply `.github/firecracker/patches/*.patch` in order before configuring to
include the balloon/reclaim changes; the example above is config-only.

To do a full build you'll also need `gcc make flex bison bc libssl-dev libelf-dev kmod perl`
(plus `gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu` for arm64) and then
`make -j vmlinux` on x86, or `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j Image`.

## License

Config fragments and workflow are yours to use (kernel itself is GPL-2.0, built from `linux-stable`).
