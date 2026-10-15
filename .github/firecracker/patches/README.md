# Kernel patches

Quilt-style `*.patch` files applied (in lexical order) to the `linux.git`
checkout before `make olddefconfig`, via the `Apply kernel patches` step in
`.github/workflows/build-firecracker-kernel.yml`.

## Current set

- `0001-virtio-balloon-aggressive-inflate.patch` — reclaim **faster**:
  - inflate/deflate batch `256 -> 1024` pfns per `tell_host()` round-trip
    (1 MiB -> 4 MiB on 4K pages, 4x fewer round-trips; the pfn array grows
    from 1 KiB to 4 KiB),
  - allocation-failure retry sleep `200ms -> 20ms`,
  - OOM deflate `256 -> 1024` pages per event.
  - No new Kconfig; no feature-bit changes. Tune by editing the defines.
- `0002-balloon-reclaim-cache-aggressive.patch` — reclaim **everything**:
  - `balloon_page_alloc()` (`mm/balloon_compaction.c`):
    `__GFP_NORETRY`/`__GFP_NOMEMALLOC` -> `__GFP_RETRY_MAYFAIL` +
    `__GFP_HIGH` | `__GFP_MEMALLOC`, so balloon allocations run
    direct reclaim (drop clean file cache, shrink reclaimable slab)
    AND dip into watermark reserves with priority, while still
    failing instead of OOM-killing. `__GFP_NOWARN` is kept.
  - Tradeoff: while the host inflates hard the guest keeps almost no
    safety margin - keep ~10-20 MiB host-side slack plus
    `deflate_on_oom`. Revert this hunk for stock fail-fast behaviour.
- `0003-balloon-drop-caches-on-stall.patch` — drain cache in one shot:
  - new `balloon_drop_caches()` in `fs/drop_caches.c` (= `echo 3 >
    /proc/sys/vm/drop_caches`, declared in `include/linux/swap.h`),
    called from `fill_balloon()` on the first stalled allocation with one
    retry before giving up the batch. Converts cached memory into
    balloonable memory without grinding page-by-page through reclaim.
  - Needs `CONFIG_SYSCTL=y` (pinned in both fragments). Tradeoff: while
    the host inflates hard, guest cache is forfeit.
- `0004-balloon-reporting-hinting-aggressive.patch` — report fragmented
  free and hint harder (needs host `F_REPORTING` / `F_FREE_PAGE_HINT`,
  i.e. Firecracker `free_page_reporting` / `free_page_hinting`):
  - page reporting order `pageblock -> 9` (2MB on 4K pages; ARM64/64K keeps
    its order-5 special-case), so free memory that can't coalesce into a
    full pageblock is still reported. 2MB matches host THP size, avoiding
    splits. Still overridable via `page_reporting.page_reporting_order=`.
  - free-page-hint blocks: `__GFP_NORETRY`/`__GFP_NOMEMALLOC` ->
    `__GFP_RETRY_MAYFAIL` | `__GFP_HIGH` | `__GFP_MEMALLOC`, so 4MB
    hint blocks survive pressure via reclaim plus reserves
    instead of failing fast, while still failing rather than OOM-killing.
- `0005-balloon-bigger-batches.patch` — maximum batches, biggest chunks:
  - batch `1024 -> 8192` pfns (4 MiB -> 32 MiB per `tell_host()` on 4K
    pages, 16x fewer round-trips than stock; pfn array 4 KiB -> 32 KiB,
    still a cheap probe-time kzalloc - larger risks high-order alloc
    failure which would kill the balloon entirely),
  - OOM deflate `1024 -> 32768` pages (up to 128 MiB freed per OOM event
    via 0009's drain loop; one `leak_balloon()` batch caps at 32 MiB).
- `0006-balloon-flush-dirty-on-stall.patch` — close the dirty-cache gap
  (the classic "guest uses 30 MiB, host shows 130 MiB" when the rest is
  dirty + clean cache, which `drop_caches` alone cannot drop):
  - on first stall, `wakeup_flusher_threads(WB_REASON_VMSCAN)` so dirty
    pages start cleaning, then `balloon_drop_caches()` + retry,
  - on second stall, `usleep_range(500, 1500)` (~1ms) for that writeback,
    drop + retry once more before giving up the batch; retry sleeps
    `20ms/10ms -> ~1ms` (`msleep(10)` is really 12ms at `HZ=250`).
    The outer `update_balloon_size` work requeues until `diff==0`,
    so yielding fast and retrying beats sleeping.
  - needs `<linux/writeback.h>` (6.18 single-arg wakeup).
- `0007-mm-lower-floor-for-microvms.patch` — floor the unreclaimable
  minimum (all runtime-tunable, so host policy can still override):
  - `min_free_kbytes` curve quartered (`sqrt(lowmem*16)/4`, cap
    `262144 -> 65536`; 128-1024 MiB guests free ~2-3 MiB),
  - `vfs_cache_pressure` `100 -> 1000` (dentry/inode slab becomes
    balloonable much faster),
  - `dirty_background_ratio` `10 -> 1`, `vm_dirty_ratio` `20 -> 2`
    (dirty pages clean almost immediately, hence droppable/balloonable
    sooner; sustained-write throughput pays the price).
  - Tip: with a swap device, also raise `vm.swappiness` at runtime
    (`sysctl vm.swappiness=100`) so reclaim-allowed balloon allocations
    can convert anon into host-reclaimable memory.
- `0008-balloon-reporting-finer-faster.patch` — strand nothing:
  - reporting order `2 -> 0` (4 KiB on 4K pages: every free page is
    reported, at the cost of more per-report virtqueue traffic),
  - reporting delay `2s -> 100ms` so host RSS tracks guest frees almost
    immediately (more reporting wakeups under churn).
  - Honest note: Firecracker's classic balloon path (`amount_mib` +
    stats) currently ignores `F_REPORTING`/`F_FREE_PAGE_HINT`, so on
    Firecracker the 0002/0003/0006/0007 reclaim paths are what move host
    RSS; this patch helps hosts that do enable reporting.
- `0009-balloon-drain-oom-budget.patch` — survive spikes: loop
  `leak_balloon()` until the 128 MiB OOM budget is freed or no progress
  is made (one call moves at most one 32 MiB batch). A spiking guest
  gets up to 4x32 MiB back per OOM event instead of OOM-killing in a
  loop while the host re-inflates. No-op when the balloon is empty.

## Host side (Firecracker) - required for the savings

Guest patches only raise the *ceiling* of what a balloon target can
take; the host must still ask. For minimal host RSS (`free -m` says
30 MiB used, host RSS near that + ~5-15 MiB Firecracker/QEMU overhead):

```json
{
  "balloon": {
    "amount_mib": 0,
    "deflate_on_oom": true,
    "stats_polling_interval_s": 1
  }
}
```

Poll `balloon stats` (`mem_free`, `mem_available`, `caches`) per VM and
set `amount_mib = max(0, vm_ram - (mem_available + slack))` with a
~10-20 MiB slack so the guest never OOMs; `deflate_on_oom` (patched to
drain up to 128 MiB/event) is the safety net. Without a balloon target the guest
cannot free host memory by itself - that is why "guest 30 MiB / host
130 MiB" happens with no balloon policy.

## Adding a patch

1. Diff against the `linux-6.18.y` branch (`a/` + `b/` paths, `patch -p1`).
2. Name it `NNNN-short-description.patch` so ordering is explicit.
3. Keep hunks minimal with stable context (driver defines shift rarely,
   function bodies churn) so weekly `linux-6.18.y` rebuilds still apply.
4. The workflow uses `patch -p1 --forward`, so re-running is idempotent.
