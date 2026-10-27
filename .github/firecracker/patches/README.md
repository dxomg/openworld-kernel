# Kernel patches

Applied in lexical order to Linux `linux-6.18.y`, before `olddefconfig`.
The workflow uses `patch --batch --fuzz=0 -p1 --forward`; a changed upstream
context fails the build rather than silently applying a fuzzy hunk. Apply
once to a fresh checkout (reapplying is an error, not an idempotent operation).

## Stock-Firecracker hosting profile

| Patch | Effect |
| --- | --- |
| `0001-virtio-balloon-aggressive-inflate.patch` | Keep **256 PFNs / 1 MiB** per descriptor. Submit partial progress immediately, back off 200 ms on zero progress outside the balloon mutex. Drain up to **128 MiB** across batches on a global OOM notification. |
| `0002-balloon-reclaim-cache-aggressive.patch` | Use `__GFP_RETRY_MAYFAIL` for sustained direct reclaim while retaining `__GFP_NOMEMALLOC`. Normal reclaim ages cache, shrinks slab and drives writeback; the balloon cannot consume emergency reserves or invoke the OOM killer to satisfy its allocation. |
| `0003-page-reporting-fine-grained.patch` | Set the reporting **core default to order 0**, with a **100 ms** event-driven reporting delay. Boot/runtime `page_reporting.page_reporting_order` overrides remain available. |
| `0007-mm-lower-floor-for-microvms.patch` | Quarter the calculated minimum free reserve; default `vfs_cache_pressure=1000`, `dirty_background_ratio=1`, `dirty_ratio=2`. These favor density over metadata-cache retention and sustained-write throughput, and remain sysctl-tunable. |

The former 64 MiB descriptor and global-cache-drop patch stack has been
replaced. These corrections matter for hosting:

- **Oversized descriptors do not fall back to smaller batches.** Stock
  Firecracker v1.17.0 caps inflate descriptors at 256 PFNs and skips oversized
  ones. The guest can wait indefinitely for the acknowledgement. Keeping the
  upstream array size also avoids embedding a 64 KiB PFN array in the
  `kzalloc`-allocated balloon structure.
- **Driver `prdev->order=0` means unspecified.** Linux's registration path
  otherwise falls back to `pageblock_order`; changing the reporting core's
  default actually enables single-page reports.
- **A page-count sum is not a cache-content fingerprint.** Dirty pages becoming
  clean need not change the file-page count. The removed deduplication could
  suppress useful work, while full cache drops could evict hot data globally.
- **Free-page hints must remain cheap.** Their upstream fail-fast allocation
  flags are retained; asking for hints should not consume emergency reserves
  or drive high-order direct reclaim.

## Host setup and policy

See the root [README](../../../README.md#ballooning-on-stock-firecracker).
Stock **Firecracker v1.17.0** supports `free_page_reporting`; a custom VMM is
not required. Older releases may only support traditional ballooning.
Reporting returns already-free physical backing without shrinking guest
capacity. Explicit balloon targets can additionally drive cache reclaim.

`deflate_on_oom` is a last resort for global allocation OOMs, not a guarantee
against cgroup OOMs or application allocation failures. The host must lower
the target on pressure and leave enough capacity for workload bursts. The
128 MiB drain is an upper bound, limited by the currently releasable balloon.
Zswap needs an active backing swap device/file to reclaim anonymous memory;
these kernels enable its shrinker so cold compressed pages can be written out.

Measure host RSS, guest available memory, major faults, allocation stalls,
write latency and application p99 during inflate/deflate cycles. Smaller
watermarks and faster reporting are density tradeoffs, not throughput wins
for every workload.

## Upstream references

- [Firecracker v1.17.0 balloon constants](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/devices/virtio/balloon/mod.rs)
- [Firecracker v1.17.0 descriptor handling](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/devices/virtio/balloon/device.rs)
- [Firecracker v1.17.0 balloon configuration](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/ballooning.md)

## Adding a patch

Diff against the same `linux-6.18.y` checkout as the build, with `a/` and `b/`
paths. Keep stable context and lexical ordering. Resolve and validate both
configs, compile both architectures, and exercise balloon convergence,
reporting and memory-pressure recovery on the target Firecracker release.
