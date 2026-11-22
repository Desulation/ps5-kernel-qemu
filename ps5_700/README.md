# PS5 7.00 "manu" kernel — QEMU boot profile

A second boot profile for this kit, targeting the **7.00_t_release_manu** prototype
kernel (`Prototype Globe PS5 Devkit Files/700_t_manu_kernel.elf`, `r205498`, built
Jul 11 2023) instead of the 13.60 live dump the main kit uses.

Status: **REACHES THE KERNEL BANNER.** The real Sony 7.00 kernel prints its own
`SIE CONFIDENTIAL` / copyright banner inside QEMU. Cleared so far: console routing,
SMAP, pmap_bootstrap, PSP mailbox + TMR secure-init, vmmcall, and the PS5 memory-profile
carve (via an inflated SMAP — see below). Prints the **full banner** (build string included) and now proceeds **past VM/UMA bootstrap** into
the PS5 memory-region mapping, stopping at a PS5 page-table-memory **pool** exhaustion
(`panic: page table memory exhausted 0`). 13 image/loader patches. Not yet as far as the 13.60
profile (late SYSINIT). See **Past the UMA crash** and **Frontier/Next steps**.

Full banner captured in `frontier_serial.txt`:
```
SIE CONFIDENTIAL
Copyright (C) 2022 Sony Interactive Entertainment Inc.
...
FreeBSD is a registered trademark of The FreeBSD Foundation.
r205498/releases/07.00_t_release_manu Jul 11 2023 19:04:20 amd64
mDBG: Kernel Build ID = 2e32fc7f88d38426
```
The `r205498/releases/07.00_t_release_manu` build string matches the kdata boot log exactly.

## Why this profile differs from the 13.60 kit

| | 13.60 kit | 7.00 profile |
|---|---|---|
| Input | raw live RAM dump (pre-relocated at `0x9e3f0000`), reconstructed with 7,621 image patches | real linked ELF; flattened + `R_X86_64_RELATIVE` relocs applied |
| Load base | phys `0x1e3f0000` / virt `0xffffffff9e3f0000` | phys `0x210000` / virt `0xffffffff80210000` |
| Handoff | btext `0xffffffff9e642a90` | C entry (ELF e_entry) `0xffffffff80450280` |
| DMAP window | PML4 slot `0x11C`, PDPT `0x1DC` | PML4 slot `0x100`, PDPT `0x0` (base `0xffff800000000000`) |
| In-memory patches | 676 (mostly `rdmsr`/`vmmcall`) | 0 — all code patches folded into the image at build time |
| Image size | `0x3e34000` | `0x44ca000` |

Addresses were found by bridging 13.60 → 14.22 → 7.00 through the function-match
tables built earlier (`funcdiff_700manu_vs_1422/`), then confirmed by disassembly.

## Files

- `build_kernel_700.py` — ELF → flat image at `0x210000`, applies relocations and the
  code patches below, writes `kernel_raw_700.bin`.
- `trampoline_700.asm` → `trampoline_700.elf` — multiboot loader (copy, page tables,
  metadata, hand off to `0x80450280`). Built with:
  `nasm -f elf32 -Ips5_700/ -o t.o trampoline_700.asm && ld.lld -m elf_i386 -T ../wp3_boot/tramp.ld -o trampoline_700.elf t.o`
- `meta_700.inc` — preload metadata: NAME/TYPE + Sony SMAP record (type `0x9001`), 3 RAM entries 0–2 GB.
- `params_700.inc` — phys boot-param block (reused from 13.60; not yet tuned for 7.00).
- `patches_700.inc` — empty (code patches live in the image).
- `run_700.sh` — boot in QEMU, serial → `serial_700.log`. Usage: `bash ps5_700/run_700.sh [seconds]`.
- `frontier_faults.txt` / `frontier_int.log` — the current stopping point (QEMU `-d int`).

Requires `nasm` and `ld.lld` (installed via Homebrew: `brew install nasm lld`) and `qemu-system-x86_64`.

## Crash-sites cleared so far

Trampoline (`trampoline_700.asm`):
- **DMAP window** mapped at PML4 `0x100` / PDPT `0x0` (7.00's `0xffff800000000000`), not 13.60's `0x11C`.
- **Scratch window** at PML4 `0x15F` → 1 GB of scratch RAM (phys `0x40000000`): covers the
  high KVA `pmap_bootstrap` zeroes early while still on the loader's page tables.
- Patch-loop zero-guard; metadata with the Sony SMAP record.

Image (`build_kernel_700.py`, bridged 13.60→14.22→7.00, confirmed by disassembly):
1. **Console** — `cnputc` (`0x80cd59f0`) redirected to QEMU debugcon (`out 0x402`); `putchar` je
   at `0x806cf453` NOP'd; `putc`/`null-putc` stubs redirected. Makes all kernel output visible.
2. **DELAY busy-loop** (`0x806a5d6c`) — pre-TSC-calibration `while(--n)` spin → `xor eax,eax` (TSC path kept).
3. **vmmcall** — all 21 `0f 01 d9` hypercalls in the text segment → `xor eax,eax; nop` (TCG `#UD`s on them).
4. **PSP mailbox send** (`0x8050b6f0`) → `return 0`: no PSP in QEMU, else it spins on a dead status reg.
5. **psp_mbox_map_tmr** (`0x8050b570`) → `return 0` (success): else `commit_registered_static_tmr_region()`
   `panic: TMR ERROR` (then a `ud2`, since the panic is noreturn).

6. **Memory profile** — `meta_700.inc` SMAP inflated to **32 GiB**. The profile carve
   (`"Memory param is found"` @ `0x80bce540`) reserves ~25 GB (app 13 GB + tool 12 GB); its
   allocator (`0x80bc8b30`) is pure region bookkeeping (no touch), so a large SMAP lets the
   reservation succeed without real RAM. getmemsize/vm sizing scale off this, but that bites
   only later (vm_page_startup is a post-banner SYSINIT). Giving QEMU 32 GB instead is not
   viable on a 16 GB host.
7. **nanobug log init** (`0x80993580`) → `ret`: it takes a spin lock that deadlocks (same class
   as the frontier below); non-essential logging, skipped.

Boot order cleared: DELAY → SMAP → DMAP #PF → pmap_bootstrap high-KVA zeroing → vmmcall #UD →
PSP mailbox → PSP TMR → memory-profile carve → **BANNER** → console-lock deadlock (current stop).

8. **msgbuf/syslog writer** (`0x80ddfea0`) → `return 0`: it locks a per-sink mtx (`[r15+0x40]`)
   that isn't `MTX_UNOWNED` this early, so `_mtx_lock_spin` (`0x806e3890`) deadlocks (its timeout
   path only `DELAY(1)`s and re-spins). Console output already works via the direct `cnputc`
   redirect; skipping the msgbuf tee unblocked the **full banner**. (Same deadlock class also hit
   the `nanobug` init, patch 7.)

## Frontier (current stop) — and the SMAP tradeoff

```
#PF CR2=0 (null deref) at 0xffffffff80679a04, in a UMA function
  (strings "UMA: Increase vm.boot_pages", "Bad link in us_type._us_link")  -> #DF
```
This is **UMA (kernel zone allocator) / VM bootstrap**, and it exposes the cost of the SMAP
shortcut: inflating physmem to 32 GB cleared the memory-profile carve, but now `vm_page_array`
and UMA size themselves for 32 GB and exhaust the early page pool against 2 GB of real RAM
(hence the literal `UMA: Increase vm.boot_pages`). This is exactly why the 13.60 kit **shrank the
memory profile** (keeping SMAP ≈ 2 GB) rather than inflating it.

## Why the memory profile can't just be "shrunk" (investigated 2026-11-21)

Tried the obvious shrink — SMAP back to 2 GB and the carve's app-memory request (`r14` at
`0x80bced89`) capped to 64 MiB. It still `panic: Failed to allocate application memory`. The
allocator `0x80bc8b30` is called with `rdx = 0x100000000` (a 4 GB floor) and only accepts a
region **above 4 GB** — PS5 application memory lives high in the physical map. A 2 GB physmem has
no such region, whatever the size. So shrinking the *size* is not enough; the PS5 physical-memory
*layout* would have to be relocated low — a real RE sub-project, not a constant tweak. That is why
this profile keeps the **32 GB SMAP**: it is what gives the carve a high-address region. The cost
is VM/UMA sizing for 32 GB on a 2 GB host (the frontier below).

## Frontier (current stop) — UMA/VM bootstrap under a 32 GB Maxmem

```
#PF CR2=0 (null deref) at 0xffffffff80679a04, in a UMA keg/slab list walk
  (same func carries "UMA: Increase vm.boot_pages", "Bad link in us_type._us_link")  -> #DF
```
With Maxmem = 32 GB (from the inflated SMAP), `vm_page_array` (~0.8 GB) and the early UMA pool are
sized for 32 GB and exhaust against 2 GB of real RAM. The banner prints before this; getting past
it is the start of the post-banner SYSINIT march.

### Past the UMA crash: fake app-mem base + 2 GB SMAP (2026-11-21)

The UMA crash (below) came from inflating the SMAP to 32 GB (so the carve's app allocator could
find its required >4 GB region), which then sized VM/UMA for 32 GB and exhausted the page pool on a
2 GB host. Resolved differently: keep the **SMAP at 2 GB** (so `Maxmem` and VM are sane) and make the
carve's app allocator return a **fake 4 GB base** instead of searching physmem —

- `0x80bcedae`: `call 0x80bc8b30; test rax,rax; je panic` → `mov rax, 0x100000000; nop*4`
- `0x80bced89`: cap the app-region size to 64 MiB (`mov r14, 0x4000000`) so few page tables are built.

This works because QEMU TCG reads unassigned physical addresses (the fake region at 4 GB) as 0
without faulting, and nothing touches app memory during boot. Result: the **full banner prints and
boot proceeds past UMA** into the PS5 memory-region mapping, now stopping at:

```
panic: page table memory exhausted 0   (then recursive #PF at 0x80c00dcb)
```
This is pmap's **page-table-page pool allocator** (`0x80ab9620`, pool array base `0x844177e0`): it
pulls a page from pool 0's freelist (`[pool+0x20]`), which is empty — i.e. pmap ran out of PT pages
while mapping the carve's memory regions. Confirmed it's the whole pmap subsystem (pmap_bootstrap
`0x80aa3480` sets the DMAP slot `0x100`/window constants; dozens of `pmap_enter`/`pv_next` callers
consume the pool). **Root cause is the partial cap:** only the *app* region was shrunk (fake base +
64 MiB); **tool/direct/flexible/workspace memory are still their full multi-GB profile sizes**, and
pmap exhausts its PT-page pool mapping those. Two ways forward:
- cap *every* carve region small (find each size in the `0x80bce540` carve / `0x80bce130` setup), or
- enlarge pmap's PT-page pool fill (in pmap_bootstrap `0x80aa3480` / the pool init) so it doesn't run dry.
Either is a real sub-task; the fake-base shortcut trades the VM-sizing problem for this mapping one.
The principled alternative remains the consistent-config VM surgery (32 GB SMAP + clamp before
`vm_page_startup`), where every pool is just "sized for 32 GB."

Alternative principled path (kept for reference): the VM surgery below (consistent 32 GB config for
the carve, then clamp `phys_avail`/`Maxmem` to 2 GB before `vm_page_startup`) avoids the fake-base
inconsistency but is more work.

### UMA crash, exactly (seen with the 32 GB-SMAP approach)

`startup_alloc` (`0x80679920`) hands out early **boot pages** for UMA slabs; it walks a boot-page
freelist and, when a zone asks for more contiguous pages than remain, runs the list off its end and
null-derefs at `0x80679a04` (its own `"UMA: Increase vm.boot_pages"` panic is the next block). The
demand is high because early UMA zones size off `Maxmem` = 32 GB. So the root is the 32 GB `Maxmem`.

### Why the Maxmem clamp is not a one-liner

`getmemsize` (`0x80bc73d0`) reads the SMAP (records `0x9004`/`0x9001`) and feeds each RAM region to
`0x80bc88e0`, which builds `phys_avail[]` / `Maxmem`. **The PS5 memory carve (`0x80bce540`) and the
VM both consume that same `phys_avail`.** Clamping it at ingest (dropping the >4 GB regions) starves
the carve of the high region it requires and re-breaks it. Genuine decoupling means: let the carve
run on the full 32 GB `phys_avail`, then **rebuild `phys_avail[]` + `Maxmem` down to ~2 GB in the
window between the carve and `vm_page_startup`** — real VM surgery, with its own follow-on walls
(`vm_page_array`, pv zones). This is the sustained 13.60-style campaign, not a constant tweak.

## Next steps (ordered by payoff)

1. **phys_avail/Maxmem surgery:** after the carve, before `vm_page_startup`, shrink `phys_avail`
   and `Maxmem` to the real low ~2 GB so UMA/`vm_page_array` size sanely. Anchors: getmemsize
   `0x80bc73d0`, per-region adder `0x80bc88e0`, carve `0x80bce540`, startup_alloc `0x80679920`.
2. Alternatively the deeper fix: relocate the PS5 app-memory layout to low addresses so the carve
   works with a 2 GB SMAP (then no VM-sizing problem at all).
3. Then: cninit, the sysctl-registration storm, CPU/ACPI/SMP enumeration, the device-module wave —
   adding MSR/hardware-poll guards as the 13.60 worklog did — toward late SYSINIT.

`frontier_serial.txt` holds the full boot log through the banner to the current stop.

## Code cave (region PT-build size cap) + live pool diagnosis (2026-11-21)

Built a working **code cave** to cap the per-region page-table build size: `0x80517d30`
(`"[KERNEL] %s page table exhausted"`) takes region size in `rdx`; its entry (6 bytes) is
redirected to a cave in the 0xCC padding at `0xffffffff80210100` that re-runs the clobbered
prologue, caps `rdx` to 64 MiB, and jumps back. Verified it executes and changes behaviour
(an aggressive 1 MiB cap altered the downstream output), i.e. the cave mechanism works.

**But it does not clear the stop.** Live kernel debugging (patched the panic site to `hlt`,
attached lldb via the QEMU gdbstub, read the pool struct at `0xffffffff844177e0`) shows pmap
PT-page pool 0 is backed by **8192 pages** (`+0x50 = 0x2000`, ~128 MiB) at phys `0x4710000`
(just past modulep), freelist (`+0x20`) emptied. The ~14 pmap consumers mapping the carve
regions collectively outrun 8192 pages — capping any one builder isn't enough (even a 1 MiB
cap still exhausted it). The fake 4 GB app base likely adds cost (deep PT hierarchies for a
high VA). **Real fix:** enlarge the pool's reserve/count (set in pmap_bootstrap `0x80aa3480`;
the 0x2000 count is computed, not a literal — not yet pinned), or reconsider the fake base so
mappings stay low/dense. Debug technique that worked: `hlt`-patch the panic + gdbstub + read
the struct — reusable for the next pool/stop.

## Pool-enlarge attempt (2026-11-21): wrong reserve

Tried enlarging the pmap PT reserve in pmap_bootstrap at `0x80aa36a9` (`shr rcx,0x15` computes
~span/2MB reserve pages; patched to `0x13` for ×4). **No effect** — identical exhaustion. So
`0x80aa3680`'s reserve is the kernel's *own* page tables (PML4/PDPT/PD for the kernel map), NOT the
"pmap container" pool at `0x844177e0` that's exhausting. That container pool (phys base `0x4710000`
= modulep+0x10000, count `+0x50 = 0x2000` = 8192 pages, region VA `0xffffafe106741000`) is filled by
a separate, still-unlocated PS5 pmap-container init. Enlarging it needs that init; reserve-bump at
0x80aa3680 is a confirmed dead-end. The working size-cap cave (above) remains in place but is
insufficient alone. This pool wall is the current hard blocker.

## Pool-enlarge: found the init, but the pool can't be grown enough (2026-11-21)

Located the real "pmap container" pool init in pmap_bootstrap: `0x80aa36f7` stores the page count
(`eax`) to `pool+0x50`, and `eax = [r13(PML4)+rdx(PDPT+PD)+0x20]` where the dominant PD term is
`span>>21` (`shr rcx,0x15` at `0x80aa36a9`). Patching that shift to `0x13` (×4) **does** enlarge the
pool — verified live: `pool+0x50` went `0x2000` → `0x8034` (~32,820 pages). **But it still exhausts
the freelist.** At ×4 the pool is already ~512 MiB of page tables; for it to suffice a mapping would
need the pool to cover ~256 GB of address space (32,820 PT pages × 8 MiB/PT) — impossible to back on a
2 GB host. So the pool is *not* the real problem: some mapping is enormous. This is the fake-base + 2 GB
config producing an inconsistent, huge mapping (the uncapped tool/direct/flexible carve regions and/or
the fake 4 GB app base mapped at fine granularity). Pool-enlarge is therefore a dead-end; the real fix
is reducing the mapped span — the consistent-config VM surgery (32 GB SMAP + clamp before
vm_page_startup) or a full PS5 memory-layout rework. Tooling proven this session: hlt-patch a panic +
QEMU gdbstub + lldb read/watchpoint of kernel structs.

## VM surgery: reverted to a consistent 32 GB config (2026-11-21)

Removed the fake-base + size-cave + app-cap hacks (they made the config inconsistent → an
impossibly large pmap pool). The profile now runs a **consistent 32 GB SMAP**: the carve, pmap
and its PT-page pool all size coherently (no pool exhaustion). 11 patches, no cave. With PS5's
16 KB pages `vm_page_array` for 32 GB is only ~200 MB (fits 2 GB), so the one thing 32 GB still
breaks is the early UMA boot-page pool:

```
#PF CR2=0 at 0x80679a04 (UMA startup_alloc) -> #DF   ("UMA: Increase vm.boot_pages")
```
This is the clean, consistent frontier. Fixing it needs either:
- **bump `boot_pages`** — the boot-page freelist head is `0x83ed79c0` (read by startup_alloc at
  `0x806799bc`); its fill/count (uma_startup) isn't located yet (not a rip-relative store near the
  head; likely written via a base register), and the `vm.boot_pages` sysctl oid wasn't found as a
  static pointer; or
- **clamp `Maxmem`** after getmemsize so UMA/VM size for ~2 GB. But the carve and VM **share
  `phys_avail`** (both go through the inserter `0x80bc88e0`), so phys_avail can't be clamped at
  ingest without breaking the carve — the clamp must target the `Maxmem` global specifically
  (global not yet located; getmemsize is `0x80bc73d0`, region-adder `0x80bc88e0`).

Both require globals buried in PS5 pmap/UMA code that resisted static+dynamic location this
session. This is the sustained campaign; the consistent 32 GB config is the right foundation for it.
