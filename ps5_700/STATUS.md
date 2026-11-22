# PS5 7.00 manu kernel → QEMU — STATUS / handoff (2026-11-21)

Single authoritative snapshot so the session can be compressed and resumed cold.
Full detail/history is in `README.md`; this is the "where we are + how to continue".

## One-line status
The real Sony **7.00_t_release_manu** kernel boots in stock QEMU, **prints its full banner**
(`r205498/releases/07.00_t_release_manu Jul 11 2023`), **clears the UMA boot-page wall**, and now
stops at **`panic: page table memory exhausted`** inside `pmap_growkernel`. Config is consistent
(32 GB SMAP), 11 image patches, no hacks. **You MUST run QEMU with `-m 32G`** (run_700.sh does).

## 2026-11-21 (session 3) — two walls resolved/characterized
- **UMA wall SOLVED.** Root cause (proved live via gdbstub + QEMU `gva2gpa`): with a 32 GB SMAP,
  `vm_page_array` / UMA boot-pages are placed at the TOP of phys (~31 GB, GPA `0x7c0be4000`).
  Under `-m 2G` that phys is unassigned → TCG reads it as 0 and DROPS writes, so the UMA boot-page
  TAILQ links (built correctly by uma_startup at `0x806739e0`) evaporate → the `TAILQ_REMOVE`
  INVARIANTS check in `startup_alloc` (`0x80679a04`, `cmp [prev],elm` with prev==0) #PFs at CR2=0.
  **Fix: boot with `-m 32G`.** QEMU host-lazy-backs it (only ~hundreds of MB resident), so the
  high structs become real RAM. Shrinking the SMAP to 2 GB does NOT work — the 7.00 *devkit* carve
  runs before the banner and needs the full 32 GB (2 GB faults pre-banner). So: 32 GB SMAP + `-m 32G`.
- **New frontier = `pmap_growkernel` PT-page exhaustion.** The region PT builder (`0x80517d30` →
  `pmap_growkernel` `0x80aa6990` → allocpte `0x80aa6be0` → pool alloc `0x80ab9620`) maps the carved
  PS5 memory regions into KVA. `pmap_growkernel` allocates **one PT page per 2 MB** of KVA span at
  16 KB-leaf granularity. The span is huge (>64 GB of KVA), so the per-domain PT-page pool
  (`0x844177e0`, free-head +0x20, base phys `0x4710000`, reserve +0x50 = **8241 pages**, sized by
  the phys-top helper `0x80bc8ea0` ≈ 16 GB via `phys/2MB`) drains and panics at `0x80ab9758`.
- **Pool-growth proven infeasible (clean config).** `shr 0x15→0x13` at `0x80aa36a9` (×4 reserve →
  ~32 K pages = ~64 GB coverage) STILL exhausts. Confirms STATUS's earlier (fake-base) finding.
- **What finishing THIS wall needs:** reduce consumption, not grow the pool — either make the region
  builder map the big linear regions as **2 MB superpages** (PDE.PS=1 → 512× fewer PT pages; look at
  the flag assembly in `0x80518360`+ and the leaf-vs-PDE decision in `pmap_growkernel`), or stub the
  eager KVA mapping of the PS5 app/GPU regions (not needed to reach SYSINIT). Both are deep pmap work.
- **Superpage attempt (2026-11-21, session 3) — cheap levers exhausted, confirmed non-convergent:**
  - The region mapper is plain per-page `pmap_enter` (`0x80518300`+ assembles a 16 KB-leaf PTE in r15d;
    no PS/2 MB path). It maps into the kernel submap based at **`VM_MAX_KERNEL_ADDRESS` = `0xffffff8000000000`**
    (stack VAs `0xffffff800a2/a4 00000`), spanning **>256 GB of VA**.
  - The superpage-enable byte `0x84417cc0` is **already 1** (set in pmap_bootstrap `0x80aa3a2f`); growkernel's
    2 MB logic is already active, yet it still builds a full PT skeleton because entries come in per-16 KB-page.
  - **Pool growth does NOT converge (measured live).** `shr 0x15→0x11` at `0x80aa36a9` (×16) grows the
    reserve to **131136 pages** (confirmed: `[0x84417830]=0x20040`) = ~256 GB coverage, and it STILL
    panics `page table memory exhausted`. (×4 and ×16 both fail; ×1=8241.) So no feasible pool covers it.
  - Therefore the ONLY path forward is true consumption reduction: real 2 MB-PDE superpage mapping for the
    big regions, or stubbing/compacting the eager KVA region map — a multi-step pmap sub-project, not a
    one-byte patch. (Trampoline gotcha hit again: a stale/short `ld.lld` output drops the multiboot magic
    at file `0x1000` → `qemu: linux kernel too old to load a ram disk`; always verify magic after rebuild.)
- **"Stub the region map" attempt (2026-11-21, session 3) — the consumer is ESSENTIAL kmem, not skippable.**
  Full frame walk at the panic (via QEMU monitor from staged artifacts; see storage note): `mi_startup`
  `0x8074c060` → **kernel-arena SYSINIT `0x809c9000`** (sets up the `vmem`/`kmem` "kernel arena",
  `0x8071eb70`) → `vmem_alloc 0x807e9130`/`vmem_xalloc 0x807e91c0` → the vm-map mapper `~0x8051axx`
  (`0x8051b2ac` calls `pmap_enter`) → `pmap_enter 0x80517d30` → `pmap_growkernel 0x80aa6990` → pool
  `0x80ab9620`. So the pool drains during **core kernel-allocator init (`kmem_back`)**, which is mandatory —
  there is NO PS5 app/GPU "region map" to stub here; stubbing it breaks malloc/kmem. (Earlier sessions'
  "region PT builder" `0x80517d30` is just `pmap_enter`.)
  - **Pool count-bump is a no-op on the real fill.** ×1 (8241) and ×16 (131136, confirmed `[0x84417830]=0x20040`)
    both panic at the SAME chain and the SAME ~6 s — so raising the reserve field `+0x50` via `shr 0x15→0x11`
    does NOT actually enlarge the free list; the real fill loop (which populates the `+0x20` free-list from
    phys `0x4710000`) is elsewhere and capped. Locating that true fill is the prerequisite for ANY pool-growth.
  - **Storage gotcha (important):** `/Volumes/1TB` (external drive) intermittently reads files back as ZEROS —
    this silently corrupts `trampoline_700.elf` (magic gone → the "ramdisk race"). Fix: stage `trampoline.elf`
    + `kernel.bin` on the internal disk (scratchpad `stage/`) and run QEMU from there. Verify magic after builds.
  - Net: reaching past this needs either (a) true 2 MB-superpage kmem mapping (deep pmap work), or (b) find
    the real PT-pool fill loop AND the oversized init allocation (a struct/zone sized from 32 GB) to cap — both
    are focused RE sub-projects. Simple stubbing/pool-bumping does not work.
- **Pool-fill hunt (2026-11-21, session 3) — found the real fill; it's EDGE-balanced, not 256 GB.**
  - Real fill: free-list head `0x84417800` is set at `0x80aa43e9`; the pages are added by `0x80ab9110`,
    called twice from `0x80aa4360`+ with phys/DMAP ranges — a profile-derived `range1`
    (`[0x81937220]<<16` start, `[0x81937224]<<16` size; 0 in the static image → set at runtime by the carve,
    setter NOT rip-relative) and a fixed `range2` = DMAP[`0x64000000`,`0x64800000`] (the immediates at
    `0x80aa43b3`/`43b8`).
  - **`0x80ab9110` is a bounded HARVESTER of existing empty PT pages, NOT a raw-page adder.** Proof:
    extending `range2` end `0x64800000→0x65800000` (+16 MB) added **zero** pages — free-list length stayed
    **exactly 8079**, identical head `0xffffafe106742e60`. So range size is not the lever; the fill is capped
    by how many empty PT pages actually exist in the harvested ranges.
  - **True pool size = 8079 pages** (walked the free list live via lldb; `scratchpad/walk.py`). The reserve
    ESTIMATE `+0x50` was 8241 — the harvester under-delivers by ~162. Demand drains the pool to EXACTLY empty
    then panics on +1, so net peak demand ≈ 8080 ≈ the ~16 GB kmem/growkernel skeleton (1 PT page / 2 MB).
  - **Open fork (both plausible):** either demand is bounded at ~8080 (then we're ~1-200 pages short — add a
    few hundred real pages or shave the kmem KVA span a little and it clears), OR it's "allocate until empty"
    (then no pool suffices). Distinguishing REQUIRES testing a bigger REAL pool, which needs range1's runtime
    setter located (to enlarge the harvested region) — the count-bump at `+0x50` is a no-op on the real fill.
  - Reliable tooling now: run QEMU from staged artifacts on internal disk (`scratchpad/stage/`), gdbstub +
    `scratchpad/walk.py` to measure the free list, QEMU monitor for register/stack frame walks.
  - **range1 is ZERO at runtime (dead end for pool growth).** Read live at the fill trap:
    `[0x81937220]=[0x81937224]=0`, so the fill's first `0x80ab9110` call (range1) is a no-op; ALL 8079 pages
    come from the second call (the fixed `0x64000000` one), whose yield does NOT scale with its range
    parameter (enlarging it added 0). So `0x80ab9110` harvests a FIXED set of existing PT pages (~8079), and
    NO fill-range parameter enlarges it. There is no profile "range1 setter" to chase — pool growth via the
    fill is not viable. (`[0x84418be4]=0x15f`, `[0x84418be8]=0x184` are the DMAP PML4/PDPT indices the harvest
    and the pool's phys→KVA conversion use.) **Conclusion: the only remaining lever is REDUCE DEMAND** — shave
    the kmem/growkernel KVA span so the skeleton needs < 8079 PT pages (deficit looks like only ~100–200), or
    make that mapping use 2 MB superpages. Next: find what sizes the kmem arena / growkernel target VA.
- **CONSUMER IDENTIFIED (2026-11-21, session 3) — it's the PS5 region carve, drained in EARLY boot.**
  Measured the pool live at several trap points: 8079 right after the fill (`0x80aa43f0`, in pmap_bootstrap),
  already **1** by the carve-setup entry `0x80bce540`, and **0** by the kmem SYSINIT `0x809c9000` (which is
  therefore just the first starved caller, NOT the consumer). Frame walk at the first pool-alloc call gives the
  real chain: `hammer_time`-era `0x80bca0c4` → PS5 region loop `0x80bcee30`/`0x80bcee8a` → region mapper
  **`0x80bcf2c0`** (rounds to 2 MB, reserves via `0x80bc89f0`) → map helper `0x80aa4e80` → `pmap_map`
  `0x80ab32d0` (loop head `0x80ab33de`; uses 1 GB/2 MB superpage skips where aligned+fitting, else 16 KB +
  `allocpte` `0x80ab3df0` → pool `0x80ab9620`). The regions are NAMED (rodata): **"VSH & workspace metadata
  DMEM"**, **"VSH text"**, **"file cache"** — PS5 system regions; "file cache" is typically sized to RAM (many
  GB). Mapping them (partly at 16 KB granularity) consumes ~8078 PT pages = the whole pool.
  - **LEVER (concrete): cap these region maps.** They are not touched before SYSINIT under QEMU (same logic as
    the old fake-base trick: unmapped/short is fine until something reads them). Cap the size the region loop
    passes to `0x80bcf2c0` (or make `0x80bcf2c0` map only a stub), freeing PT pages for the rest of boot. Region
    sizes are profile-derived (descriptor table `[r15+r12+0x18]`=base, `+0x50`=size; plus globals
    `0x8446d0a0/0f0/110`). STATUS's earlier "size-cap cave insufficient" was under the inconsistent fake-base
    config; in THIS clean 32 GB config the consumer is cleanly these named PS5 regions.
  - Tooling: `scratchpad/walk.py` counts the free list at any trap; run from `scratchpad/stage/` (internal disk).
- **Region-size cap ATTEMPT (2026-11-21, session 3) — fired but did NOT clear the wall.** Hooked `pmap_map`
  entry `0x80ab32d0` via a code cave at `0x80210100` (`rsi` size > 64 MB → clamp to 2 MB). Verified args live:
  first call `rsi=0x2d000000` (720 MB, "VSH & workspace metadata DMEM") at `rdi=0xffffafe8d3000000`. The cap
  WORKED (RSS halved 868→387 MB) but the pool STILL exhausts — because the big named regions map as **2 MB/
  1 GB superpages** (RSS/backing cost, but ~0 PT-pool pages). So capping region *size* frees backing, not PT
  pages. The pool is drained by **16 KB-granular** mappings elsewhere, and it's drained **very early** (only 1
  free node left by the first `pmap_map` call). Cap reverted (was only in scratchpad; canonical build is clean).
  - **Model gap:** the pool's fill/drain doesn't fit a simple pop-the-head stack: at the fill trap (`0x80aa43f0`)
    the list head `0xffffafe106742e60` has 8079 nodes; by `pmap_map` entry the SAME head has only 1 node
    (`[head]=0`). 8078 pages vanish before the first region map, yet the head is unchanged — so either the list
    is rebuilt, or there are multiple lists, or draining is not front-pop. Pinning the true 16 KB drainer needs
    a cleaner model (e.g., watchpoint the free-head write, or count allocpte `0x80ab3df0`/`0x80aa6990` calls by
    caller). This stopped converging; next pass should nail the pool structure first, then the 16 KB source.
- **Pool structure resolved + tooling wall (2026-11-21, session 3, later).**
  - **Domain-0 only:** walked all per-domain heads (`0x844177e0 + d*0x68 + 0x20`); only domain 0 holds pages
    (8079 at the fill trap `0x80aa43f0`; 1 by the first `pmap_map` call `0x80ab32d0`). No other domain.
  - **The builder `0x80ab32d0` is a page-table FILLER**, not a plain mapper: it walks `[start,end)` and per level
    SKIPS where existing tables already hold a superpage — `test cl,0x80` @`0x80ab3444` (1 GB PDPTE.PS),
    `test dl,0x80` @`0x80ab3466` (2 MB PDE.PS) — else steps 16 KB (`add r14,0x4000` @`0x80ab3488`) and allocates
    a PT page (via allocpte `0x80ab3df0` → pool). So the ~8078 pages are spent filling **16 KB-leaf PT pages**
    across a huge range whose underlying DMAP/physmap lacks PS (superpage) bits. If that base map used 1 GB/2 MB
    superpages, the filler would skip and the pool would survive — so the real fix is **superpages in the early
    DMAP/physmap build** (or stub the 16 KB fill for the non-essential range). Both deep.
  - **Tooling wall:** TCG write-watchpoints are unusably slow (every store is checked → one `Continue` to the
    next head write exceeds a 110 s timeout), and gdbstub free-list reads have returned inconsistent lengths
    (1 vs 8079 at the same logical point), so incremental probing can't reliably pin the exact consumer order or
    bounded-vs-unbounded demand. NEXT: build a **logging cave** — patch allocpte `0x80ab3df0` to append each
    call's VA (`rsi`) to a fixed memory buffer, boot to the panic, read the buffer once → exact VA list + count.
    That gives reliable data without the flaky live probing.
- **Logging cave BUILT + model-correcting result (2026-11-21, session 3, latest).** Hooked the pool allocator
  entry `0x80ab9620` → code cave at `0x80210108` (re-runs the clobbered 6-byte prologue, `inc qword
  [0x80210100]` rip-relative — no reg clobber — then `jmp 0x80ab9626`). The cave is WRITABLE this early and the
  counter is reliable (recipe: `scratchpad/capmk2.py`). **Result: the counter reads 2** at the empty-panic
  (`0x80ab9740` hlt-trap). So the alloc API `0x80ab9620` is entered only twice before the pool is empty —
  bootstrap does NOT consume the 8079 pages through the alloc API, nor through `allocpte 0x80ab3df0` (which
  calls that API). The free list nets only ~1 page for runtime; the 1st runtime alloc takes it, the 2nd panics.
  - **Corrected model:** the ~8079 harvested pages are consumed by bootstrap's **direct** initial-table
    construction (almost certainly drawing from the bump reserve at phys `0x4710000`, count `+0x50`=8241, NOT
    the free-list API), leaving ~1 free-list page. So future work must instrument **that direct builder**, not
    the alloc/allocpte path. The free-list (`0x84417800`) is grown by a batch-push loop `0x80ab7330` (adds a
    list of pages), popped by `0x80ab9620`; neither is the 8078-page drainer.
  - This is a genuinely deep, multi-layer RE problem (each confirmed fact uncovers the next). Reliable tools now
    in `scratchpad/`: `capmk2.py` (logging cave), `walk.py`/`domwalk.py` (free-list counts), `capmk.py`
    (region-size cap). Run everything from `scratchpad/stage/` on the internal disk.
- **Direct-builder instrumentation (2026-11-21, session 3, final).** Chased the "bump reserve" consumer: its
  count `0x84417830` and index `0x8441783c` globals are **written but never read at runtime** (only set in
  pmap_bootstrap at `0x80aa36f7`/`0x80aa36e0`), so the reserve is NOT a counted bump-allocator — that model is
  also disproven. The reserve base `0x84417820` is read only in pmap_bootstrap setup (`0x80aa3716`,
  `0x80aa37e5` → registration call `0x807fa640`), not a consuming loop. So the ~8078-page consumer is embedded
  in `pmap_bootstrap`'s own table-construction flow, reached via neither the alloc API (`0x80ab9620`, hit 2×)
  nor `allocpte` (`0x80ab3df0`) nor a simple counted bump.
  - **Honest state:** isolating the exact consumer now requires a full, methodical RE of `pmap_bootstrap`
    (`0x80aa3480`, hundreds of instructions + helpers: the harvester `0x80ab9110`, the fill `0x80aa4360`, the
    page-table walker/filler `0x80ab32d0`, and the DMAP/physmap construction) — a dedicated sub-project, best
    done fresh and systematically, not more incremental live probing (which has hit diminishing returns and,
    for the free-list, inconsistent reads). The single most decision-relevant open question remains: is the
    ~258 GB-at-16 KB build BOUNDED (then a modest superpage/cap fix leaves runtime surplus) or intrinsic. The
    working logging-cave recipe (`capmk2.py`) is the right instrument for that pass (counter/ring in the cave,
    read once at a hlt-trap), applied to whatever loop the RE pass identifies as the consumer.
- Tooling note: the live `vm_page`/pool reads use the gdbstub + a hlt-trap (`f4 eb fd`) at the panic
  branch; `gva2gpa` via `-monitor tcp:127.0.0.1:5556,server,nowait` gives the GPA of any KVA.

## Build & run
```
cd ps5-kernel-qemu
python3 ps5_700/build_kernel_700.py                      # -> ps5_700/kernel_raw_700.bin
nasm -f elf32 -Ips5_700/ -o /tmp/t.o ps5_700/trampoline_700.asm
ld.lld -m elf_i386 -T wp3_boot/tramp.ld -o ps5_700/trampoline_700.elf /tmp/t.o
bash ps5_700/run_700.sh 45                                # serial -> ps5_700/serial_700.log
```
Toolchain: `nasm`, `ld.lld` (`brew install nasm lld`), `qemu-system-x86_64` (tested 11.1.3).
Source ELF: `../Prototype Globe PS5 Devkit Files/700_t_manu_kernel.elf`.
The scan/match pickles + venv used for analysis live in the PREVIOUS session scratchpad
`/private/tmp/claude-501/-Volumes-1TB-dev-Playstation5Development/e0815a1e-.../scratchpad`
(may be cleared by cleanup; regenerate with the funcdiff pipeline in
`../Prototype Globe PS5 Devkit Files/funcdiff_700manu_vs_1422/scripts/` if gone).

## Load geometry (trampoline_700.asm)
- Flat image at phys `0x210000` / virt `0xffffffff80210000` (`KERN_PHYS`/`KERN_SIZE 0x44CA000`).
- Hand off to ELF e_entry **`0xffffffff80450280`** (the true C "btext"; NOT the cr0/cr4 locore).
- DMAP window at PML4 `0x100` (base `0xffff800000000000`), PDPT idx 0.
- SCRATCH window PML4 `0x15F` -> 1 GB scratch at phys `0x40000000` (covers pmap_bootstrap's early
  high-KVA zeroing).  Scratch PT pages at phys `0x200000`/`0x201000`.
- modulep `0x4700000`, physfree `0x4701000`.  meta_700.inc: NAME/TYPE + Sony SMAP record (type
  `0x9001`) = **32 GiB** of RAM.  hammer_time `0x80bc9030`, mi_startup `0x8074c060`.

## The 11 image patches (build_kernel_700.py), all bridged 13.60→14.22→7.00 + confirmed by disasm
1. `0x805924b0` putc  -> debugcon
2. `0x80629ec0` null-putc stub1 -> debugcon
3. `0x80629ed0` null-putc stub2 -> debugcon
4. `0x806cf453` putchar je -> nop  (route console printf to cnputc)
5. `0x80cd59f0` cnputc -> debugcon  (THE thing that makes output visible)
6. `0x809cb990` console shutdown-check -> ret
7. `0x806a5d6c` DELAY uncalibrated busy-loop -> 0  (TCG-crawl)
8. `0x80ddfea0` msgbuf/syslog writer -> return 0  (early spin-lock deadlock; console works via cnputc)
9. `0x80993580` nanobug log init -> ret  (same spin-lock deadlock class)
10. `0x8050b570` psp_mbox_map_tmr -> return 0 (success)  (no PSP in QEMU; else TMR panic)
11. `0x8050b6f0` psp_mbox send -> return 0  (no PSP; else spins on dead mailbox)
Plus the builder neutralises all `vmmcall` (0f 01 d9 -> 31 c0 90) in text (TCG #UDs them).

## Boot order cleared
DELAY → SMAP(getmemsize) → DMAP #PF → pmap_bootstrap high-KVA zeroing → vmmcall #UD → PSP mailbox
→ PSP TMR → memory-profile carve → cninit → **FULL BANNER** → **[STOP] UMA boot-page pool**.

## CURRENT FRONTIER  (⚠ SUPERSEDED — this UMA wall is SOLVED, see "session 3" at top; current
## frontier is `pmap_growkernel` PT exhaustion. Kept below for the historical UMA analysis.)
```
#PF CR2=0 at 0xffffffff80679a04  (UMA startup_alloc) -> #DF   "UMA: Increase vm.boot_pages"
```
`startup_alloc` (`0x80679920`) pops the boot-page freelist (head global **`0x83ed79c0`**, read at
`0x806799bc`); it's empty. Early UMA zones (sized vs Maxmem=32 GB) drained it. `vm_page_array` for
32 GB is only ~200 MB at PS5's 16 KB pages, so this boot-page pool is the one real 32 GB cost.

## Two ways to finish (pick one) — both need a buried global
- **(A) Bump `boot_pages`.** Freelist head `0x83ed79c0`. The fill/count (uma_startup) is NOT located
  yet — not a rip-relative store near the head (likely written via a base register), and the
  `vm.boot_pages` sysctl oid is not a static pointer in the file. Best lever: live watchpoint on
  `0x83ed79c0` write to catch the fill code (see Tooling). Then bump the loop count / reserve.
- **(B) Clamp `Maxmem`** after getmemsize so UMA/VM size for ~2 GB while the carve keeps 32 GB.
  Constraint discovered: the carve and VM **share `phys_avail`** (both go through the inserter
  `0x80bc88e0`), so you CANNOT clamp phys_avail at SMAP ingest without breaking the carve — the
  clamp must target the `Maxmem` global specifically, after getmemsize (`0x80bc73d0`, which calls
  `0x80bcde40` to process the 0x9001 SMAP and `0x80bc88e0` to insert ranges). `Maxmem` global not
  located yet; look in `0x80bcde40` for the "highest page" store.

## Dead-ends (do NOT repeat)
- 2 GB SMAP + fake 4 GB app base + size-cave: got further (past UMA) but inconsistent → pmap PT-page
  pool (`0x80ab9620`, base `0x844177e0`) exhausts; proven it canNOT be grown enough (×4 verified
  live: count `0x2000`→`0x8034`, still empty; would need ~256 GB of PT coverage). Reverted.
- Bumping the pmap_bootstrap kernel-PT reserve (`0x80aa36a9 shr rcx,0x15`→`0x13`): enlarges a
  DIFFERENT pool (kernel's own PTs), no effect on the container pool. Dead-end.
- Capping the region PT-builder `0x80517d30` (code cave at `0x80210100`): works mechanically but
  insufficient alone. Removed in the consistent-config revert.

## Key addresses (7.00 manu)
hammer_time `0x80bc9030`; mi_startup `0x8074c060`; getmemsize `0x80bc73d0`; SMAP processor
`0x80bcde40`; phys_avail inserter `0x80bc88e0`; carve ("Memory param is found") `0x80bce540`;
profile setup `0x80bce130`; app allocator `0x80bc8b30` (4 GB floor rdx=0x100000000); pmap_bootstrap
`0x80aa3480`; pmap PT-page pool alloc `0x80ab9620` / array `0x844177e0`; region PT builder
`0x80517d30`; UMA startup_alloc `0x80679920` / boot-page head `0x83ed79c0`; cnputc `0x80cd59f0`;
putchar `0x806cf440`; native sysent `0x810aa950`; aio_multi_wait impl `0x805b8fa0` (see sysent memory note).

## Tooling that works (reusable)
- Find consumers/structs live: patch the panic site (or a function entry) to `f4 eb fd` (hlt;jmp) so
  QEMU stays alive, boot with `-gdb tcp::1234` (NO `-S` — `-S` trips QEMU's initrd loader), then
  `lldb --batch`: `gdb-remote 127.0.0.1:1234`, read regs/memory, or restore the clobbered byte +
  `register write rip` + `watchpoint set expression -w write -s 8 -- <addr>` + `continue` to catch
  the writer. (`-d int` for first-fault RIP/CR2; `-d cpu -D file` for a spin RIP, file chardev only.)

## Files in ps5_700/
build_kernel_700.py, trampoline_700.asm/.elf, meta_700.inc, params_700.inc, patches_700.inc (empty),
run_700.sh, kernel_raw_700.bin (68 MiB, regenerable), README.md (full history), STATUS.md (this),
frontier_serial.txt (boot log through banner to the stop).


## UMA frontier — corrected model (2026-11-21, later)
Dug further with the live debugger (hlt at a func entry + gdbstub; connection is intermittent,
retry). Findings that CORRECT the simple "fixed 8192 pool" idea:
- `0x83ed79c0` (read by startup_alloc) is a lazily-built list head: it is **0** at startup_alloc's
  first call. So the boot-page list is grown on demand, not a fixed pre-fill.
- startup_alloc's empty path `0x80679a40` takes a lock `0x83dd7a18`, checks a counter/flag at
  **`0x83dd7854`** (`cmp [..],1; jle 0x80679b1a`) and dispatches via a tail-call (`[r15+0xa0]=handler`).
- The actual crash (`#PF CR2=0 @0x80679a04`) is a **null list-link** (`rax=[rbx+0x10]==0` then
  `cmp [rax],rbx`) during a later traversal — list corruption/exhaustion, not a trivial empty pool.
- The "8192-page / phys 0x4710000" pool from earlier was the **pmap container** pool (different),
  only relevant to the reverted fake-base path.

### What finishing actually needs
A dedicated, careful pass: fully disassemble `startup_alloc` (0x80679920), its refill path
(0x80679a40 → 0x80679b1a), the gate `0x83dd7854`, and `uma_startup` (the boot-page source), to find
the real limit and either raise it or clamp `Maxmem` so early UMA zones don't over-draw. This is a
focused RE sub-project; incremental probing (static scans + flaky gdbstub) has not converged. The
`Maxmem` global is still unlocated — getmemsize `0x80bc73d0` calls only the map PRINTER (`0x80bcde40`)
and the phys_avail INSERTER (`0x80bc88e0`); Maxmem is set elsewhere (likely inside the inserter's
max-tracking or a getmemsize wrapper) — find it there.
