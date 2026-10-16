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
- `0010-balloon-backoff-on-zero-progress.patch` — don't spin when stuck:
  a batch that allocated nothing sleeps the stock 200ms before the outer
  work requeues (~5Hz retries instead of ~500Hz of flusher kicks and
  system-wide `drop_caches()`). Batches with progress keep the ~1ms
  fast path, so a guest that later frees memory is still picked up
  within a fraction of a second.
- `0011-balloon-64mib-batches.patch` — maximum batches:
  - `VIRTIO_BALLOON_ARRAY_PFNS_MAX` `8192 -> 16384` (64 MiB per
    `tell_host()` round-trip on 4K pages; a 512 MiB inflate converges
    in 8 batches instead of 16, and 0009's 128 MiB OOM drain takes 2
    batches instead of 4).
  - The pfn array is embedded in `struct virtio_balloon`, so this is
    +32 KiB of static device memory, not a probe-time allocation (the
    high-order-alloc risk 0005 mentioned does not apply to 6.18's
    layout).
  - **Host must keep up**: Firecracker's `MAX_PAGES_IN_DESC`/
    `MAX_PAGE_COMPACT_BUFFER` must be raised to 16384 (done in the
    openworld Firecracker fork). A stock/prebuilt VMM *rejects*
    descriptors larger than its cap ("bogus page count, skipping") -
    with the stock 256 cap the guest's batches silently shrink back
    to an effective 1 MiB per descriptor.
- `0012-balloon-dedup-drop-caches.patch` — don't re-scan when stuck:
  - fingerprints the reclaimable set (file pages + reclaimable slab
    pages, read warning-free via `global_node_page_state()`) and skips
    the system-wide scan in `balloon_drop_caches()` when it is
    unchanged since the last drop. With 0003/0006/0010, a stuck
    balloon otherwise re-runs `lru_add_drain_all()` + a full
    superblock walk + a shrinker pass ~5x/second finding nothing new;
    this keeps the CPU free while still picking up any cache that
    actually gets added between retries. The user-facing `drop_caches`
    sysctl still forces a drop.

## Host side (Firecracker) - required for the savings

Guest patches only raise the *ceiling* of what a balloon target can
take; the host must still ask. The openworld Firecracker fork carries
the matching host side (see `openworld-firecracker`):

- a per-VM balloon device (`deflate_on_oom`, `stats_polling_interval_s
  = 1`, `free_page_reporting` / `free_page_hinting`), and
- a host-side balloon reaper that polls `GET /balloon/statistics`
  (`free_memory` / `available_memory` / `disk_caches` / `oom_kill`) and
  drives `PATCH /balloon` toward `max(0, available - floor)` with step
  limits, immediate deflate on guest OOM growth, and a faster inflate
  step while the host is memory-starved,
- the pfn caps raised to 16384 (`MAX_PAGES_IN_DESC` /
  `MAX_PAGE_COMPACT_BUFFER`) so 0011's 64 MiB batches are consumed in
  one pass instead of being rejected.

Manual equivalent (no reaper): poll balloon stats per VM and set
`amount_mib = max(0, vm_ram - (mem_available + slack))` with a
~10-20 MiB slack so the guest never OOMs; `deflate_on_oom` (patched to
drain up to 128 MiB/event) is the safety net. Without a balloon target
the guest cannot free host memory by itself - that is why "guest
30 MiB / host 130 MiB" happens with no balloon policy.

## Adding a patch

1. Diff against the `linux-6.18.y` branch (`a/` + `b/` paths, `patch -p1`).
2. Name it `NNNN-short-description.patch` so ordering is explicit.
3. Keep hunks minimal with stable context (driver defines shift rarely,
   function bodies churn) so weekly `linux-6.18.y` rebuilds still apply.
4. The workflow uses `patch -p1 --forward`, so re-running is idempotent.
