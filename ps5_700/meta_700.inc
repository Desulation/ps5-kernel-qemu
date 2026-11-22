; 7.00 preload metadata (consistent 32 GiB SMAP). IMPORTANT: boot with QEMU -m 32G.
; Why 32 GiB (not 2): the 7.00 *devkit/manu* memory-profile carve runs BEFORE the banner
; and needs the full 32 GiB; a 2 GiB SMAP faults in the carve (pre-banner). With 32 GiB,
; vm_page_array/UMA boot-pages land at ~31 GB phys. That is fine ONLY if QEMU actually
; backs it: -m 32G (host-lazy, ~hundreds of MB resident) keeps those structs in real RAM.
; With -m 2G the high phys is unassigned -> TCG reads 0 / drops writes -> the UMA boot-page
; TAILQ links evaporate -> #PF CR2=0 in startup_alloc. So: 32 GiB SMAP + -m 32G together.
; (Rec3 length 0x7fff00000 = ~32 GiB; top 0x7fff00000+0x100000.)
META_LEN equ 136
meta_blob:
    db 1,0,0,0,7,0,0,0,107,101,114,110,101,108,0,0
    db 2,0,0,0,11,0,0,0,101,108,102,32,107,101,114,110
    db 101,108,0,0,0,0,0,0,3,0,0,0,8,0,0,0
    db 0,160,76,4,0,0,0,0,1,144,0,0,60,0,0,0
    db 0,16,0,0,0,0,0,0,0,160,9,0,0,0,0,0
    db 1,0,0,0,0,192,9,0,0,0,0,0,0,64,5,0
    db 0,0,0,0,1,0,0,0,0,0,16,0,0,0,0,0
    db 0,0,240,255,7,0,0,0,1,0,0,0,0,0,0,0
    db 0,0,0,0,0,0,0,0
