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
- `0002-balloon-reclaim-cache-aggressive.patch` — reclaim **larger**:
  - `balloon_page_alloc()` (`mm/balloon_compaction.c`):
    `__GFP_NORETRY -> __GFP_RETRY_MAYFAIL`, so balloon allocations may run
    direct reclaim (drop clean file cache, shrink reclaimable slab) and
    convert cached memory into host-reclaimable memory, while still
    failing instead of OOM-killing. `__GFP_NOMEMALLOC` is kept so reserves
    are never touched.
  - Tradeoff: inflating under cache pressure costs direct-reclaim CPU and
    drops guest cache (re-reads cost I/O). Revert this hunk for stock
    fail-fast behaviour.
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
  - free-page-hint blocks: `__GFP_NORETRY -> __GFP_RETRY_MAYFAIL`, so 4MB
    hint blocks survive pressure instead of failing fast.
- `0005-balloon-bigger-batches.patch` — fewer round-trips, safer spikes:
  - batch `1024 -> 2048` pfns (4 MiB -> 8 MiB per `tell_host()` on 4K
    pages; pfn array 4 KiB -> 8 KiB),
  - OOM deflate `1024 -> 4096` pages (up to 16 MiB freed per OOM event).
- `0006-balloon-flush-dirty-on-stall.patch` — close the dirty-cache gap
  (the classic "guest uses 30 MiB, host shows 130 MiB" when the rest is
  dirty + clean cache, which `drop_caches` alone cannot drop):
  - on first stall, `wakeup_flusher_threads(WB_REASON_VMSCAN)` so dirty
    pages start cleaning, then `balloon_drop_caches()` + retry,
  - on second stall, `msleep(10)` for that writeback, drop + retry once
    more before giving up the batch; retry sleep `20ms -> 10ms`.
  - needs `<linux/writeback.h>` (6.18 single-arg wakeup).
- `0007-mm-lower-floor-for-microvms.patch` — lower the unreclaimable
  floor (all runtime-tunable, so host policy can still override):
  - `min_free_kbytes` curve halved (`sqrt(lowmem*16)/2`, cap
    `262144 -> 131072`; 128-1024 MiB guests free ~1-2 MiB),
  - `vfs_cache_pressure` `100 -> 500` (dentry/inode slab becomes
    balloonable faster),
  - `dirty_background_ratio` `10 -> 5`, `vm_dirty_ratio` `20 -> 10`
    (dirty pages clean sooner, hence droppable/balloonable sooner).
- `0008-balloon-reporting-finer-faster.patch` — don't strand fragments:
  - reporting order `9 -> 2` (16 KiB on 4K pages; order-9 still left
    sub-2MiB fragments stranded - the common state after uptime),
  - reporting delay `2s -> 500ms` so host RSS tracks guest frees.
  - Honest note: Firecracker's classic balloon path (`amount_mib` +
    stats) currently ignores `F_REPORTING`/`F_FREE_PAGE_HINT`, so on
    Firecracker the 0002/0003/0006/0007 reclaim paths are what move host
    RSS; this patch helps hosts that do enable reporting.

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
16 MiB/event) is the safety net. Without a balloon target the guest
cannot free host memory by itself - that is why "guest 30 MiB / host
130 MiB" happens with no balloon policy.

## Adding a patch

1. Diff against the `linux-6.18.y` branch (`a/` + `b/` paths, `patch -p1`).
2. Name it `NNNN-short-description.patch` so ordering is explicit.
3. Keep hunks minimal with stable context (driver defines shift rarely,
   function bodies churn) so weekly `linux-6.18.y` rebuilds still apply.
4. The workflow uses `patch -p1 --forward`, so re-running is idempotent.
