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

## Adding a patch

1. Diff against the `linux-6.18.y` branch (`a/` + `b/` paths, `patch -p1`).
2. Name it `NNNN-short-description.patch` so ordering is explicit.
3. Keep hunks minimal with stable context (driver defines shift rarely,
   function bodies churn) so weekly `linux-6.18.y` rebuilds still apply.
4. The workflow uses `patch -p1 --forward`, so re-running is idempotent.
