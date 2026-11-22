#!/usr/bin/env python3
# Build a flat, relocated, console-redirected boot image from the 7.00 manu ELF.
# Output: kernel_raw_700.bin  (flat image based at virt 0xffffffff80210000 = phys 0x210000)
import struct, sys, os, hashlib

# Source: the 7.00 manu kernel ELF (700_t_manu_kernel.elf, r205498). Supply its path as
# argument 1, or via the KERNEL700_ELF environment variable; otherwise it is looked for
# next to this script.
ELF = (sys.argv[1] if len(sys.argv) > 1
       else os.environ.get("KERNEL700_ELF")
       or os.path.join(os.path.dirname(os.path.abspath(__file__)), "700_t_manu_kernel.elf"))
if not os.path.exists(ELF):
    sys.exit("7.00 kernel ELF not found: %r\n"
             "Pass the path as argument 1, or set KERNEL700_ELF." % ELF)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel_raw_700.bin")
BASE = 0xffffffff80210000                  # lowest PT_LOAD vaddr (load == link base)

d = open(ELF, "rb").read()
e_phoff = struct.unpack_from("<Q", d, 32)[0]
e_phnum = struct.unpack_from("<H", d, 56)[0]
loads, dyn = [], {}
for i in range(e_phnum):
    p_type, p_flags, off, va, pa, fsz, msz, al = struct.unpack_from("<IIQQQQQQ", d, e_phoff + i*56)
    if p_type == 1: loads.append((off, va, fsz, msz, p_flags))
    if p_type == 2:                                    # PT_DYNAMIC
        k = off
        while True:
            tag, val = struct.unpack_from("<qQ", d, k); k += 16
            if tag == 0: break
            dyn.setdefault(tag, val)
span_end = max(va + msz for off, va, fsz, msz, fl in loads)
size = (span_end - BASE + 0xFFF) & ~0xFFF
img = bytearray(size)
for off, va, fsz, msz, fl in loads:
    img[va-BASE: va-BASE+fsz] = d[off:off+fsz]
print("segments:", len(loads), "image size %#x (%d MiB)" % (size, size//1024//1024))

# ---- apply R_X86_64_RELATIVE from DT_RELA (load bias 0, so slot = addend) ----
DT_RELA, DT_RELASZ, DT_RELAENT = 7, 8, 9
def v2f(va):                                           # vaddr -> image offset (or None)
    return va - BASE if BASE <= va < BASE+size else None
rela, relasz, relaent = dyn.get(DT_RELA), dyn.get(DT_RELASZ), dyn.get(DT_RELAENT, 24)
applied = preapplied = foreign = 0
if rela:
    for k in range(rela, rela+relasz, relaent):
        o = v2f(k)
        if o is None: continue
        r_off, r_info, r_add = struct.unpack_from("<QQq", img if False else d, 0) if False else \
            struct.unpack_from("<QQq", memoryview(bytes(img[o:o+24])) if False else bytes(img[o:o+24]), 0)
        # note: RELA entries live in the mapped image too; read from img
        r_off, r_info, r_add = struct.unpack_from("<QQq", bytes(img[o:o+24]), 0)
        t = r_info & 0xffffffff
        so = v2f(r_off)
        if so is None: continue
        if t == 8:                                     # R_X86_64_RELATIVE
            cur = struct.unpack_from("<Q", img, so)[0]
            val = r_add & 0xffffffffffffffff
            if cur == val: preapplied += 1
            else: struct.pack_into("<Q", img, so, val); applied += 1
        else:
            foreign += 1
print("relocs: RELATIVE applied %d, already-correct %d, other-type %d" % (applied, preapplied, foreign))

# ---- console redirect patches (bridged 13.60 -> 7.00 addresses) ----
DBG = bytes([0x40,0x88,0xf0, 0x66,0xba,0x02,0x04, 0xee, 0xc3])   # mov al,sil; mov dx,0x402; out dx,al; ret
def patch(va, newbytes, note):
    o = v2f(va); assert o is not None, note
    img[o:o+len(newbytes)] = newbytes
    print("  patched %#x (%s)" % (va, note))
patch(0xffffffff805924b0, DBG, "putc->debugcon")
patch(0xffffffff80629ec0, DBG, "null-putc-stub1->debugcon")
patch(0xffffffff80629ed0, DBG, "null-putc-stub2->debugcon")
patch(0xffffffff806cf453, bytes([0x90,0x90]), "putchar je->nop (all console printf -> cnputc)")
# cnputc(int c): emit directly to debugcon, bypassing the mute-gate and the
# (not-yet-registered) console list. c in edi. Guarantees early boot visibility.
DBG_EDI = bytes([0x40,0x88,0xf8, 0x66,0xba,0x02,0x04, 0xee, 0x31,0xc0, 0xc3])  # mov al,dil;mov dx,0x402;out;xor eax,eax;ret
patch(0xffffffff80cd59f0, DBG_EDI, "cnputc -> debugcon")
patch(0xffffffff809cb990, bytes([0xc3]), "console shutdown-check -> ret")
# DELAY(): neutralise the pre-TSC-calibration busy-loop (eax=us*1000; while(--eax);)
# which crawls under TCG. imul eax,edi,0x3e8 -> xor eax,eax (loop skipped, returns).
# TSC-based path (once tsc divisor is set) is left intact.
patch(0xffffffff806a5d6c, bytes([0x31,0xc0,0x90,0x90,0x90,0x90]), "DELAY uncalibrated busy-loop -> 0")

# syslog/msgbuf writer (0x80ddfea0): locks a per-sink mtx ([r15+0x40]) that isn't
# MTX_UNOWNED this early -> _mtx_lock_spin deadlocks. Console output already works via the
# direct cnputc redirect, so skip the msgbuf tee: return 0.
patch(0xffffffff80ddfea0, bytes([0x31,0xc0,0xc3]), "msgbuf/log writer -> return 0 (skip deadlock)")

# nanobug log init (0x80993580): takes a spin lock that reads as owned (lock mem != MTX_UNOWNED)
# -> _mtx_lock_spin deadlocks (its timeout path just re-spins). Non-essential logging; skip it.
patch(0xffffffff80993580, bytes([0xc3]), "nanobug log init -> ret (skip spinlock deadlock)")

# psp_mbox_map_tmr (0x8050b570): maps a trusted memory region via the PSP. No PSP in
# QEMU, so it returns an error -> commit_registered_static_tmr_region() panics (then ud2).
# Return 0 (success) so the error/panic path is never taken; no secure memory to enforce.
patch(0xffffffff8050b570, bytes([0x31,0xc0,0xc3]), "psp_mbox_map_tmr -> return 0 (success)")

# PSP mailbox send (0x8050b6f0): no PSP under QEMU -> it polls a dead status reg
# through a long TSC timeout, repeatedly. Its own timeout path returns 0; make the
# whole function return 0 immediately (xor eax,eax; ret) to avoid the multi-second spins.
patch(0xffffffff8050b6f0, bytes([0x31,0xc0,0xc3]), "PSP mailbox send -> return 0 (no PSP)")

# ---- neutralise vmmcall (0f 01 d9) in the text segment: hypercalls TCG #UDs on ----
# -> xor eax,eax; nop  (hypercall "returns" 0). Matches the 13.60 kit's approach.
tseg = next((va, va-BASE, fsz) for off, va, fsz, msz, fl in loads if fl & 1)  # exec segment
tva, toff, tsz = tseg
vmm = 0; i = toff
while True:
    j = img.find(b"\x0f\x01\xd9", i, toff+tsz)
    if j < 0: break
    img[j:j+3] = b"\x31\xc0\x90"; vmm += 1; i = j+3
print("vmmcall neutralised:", vmm)

# NOTE: the 2 GB-SMAP + fake-base + size-cave approach was reverted (it created an
# inconsistent config whose mappings needed an impossibly large pmap PT pool). We now
# run a *consistent* 32 GB SMAP (see meta_700.inc): the carve, pmap and its pool all
# size coherently. The only thing 32 GB breaks is UMA/VM early sizing on a 2 GB host,
# addressed separately below.

open(OUT, "wb").write(img)
print("wrote", OUT, "md5", hashlib.md5(img).hexdigest())
print("KERN_SIZE equ 0x%X" % size)
