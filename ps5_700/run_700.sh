#!/bin/bash
# Boot the 7.00 manu kernel profile in QEMU; serial -> ps5_700/serial_700.log
cd "$(dirname "$0")/.."
D=ps5_700
TMO=${1:-30}
SMP=${SMP:-1}
MEM=${MEM:-32G}   # MUST be 32G: the devkit carve needs 32 GiB and vm_page_array/UMA
                  # boot-pages land at ~31 GB phys, which QEMU must back (host-lazy).
QEMU=$(command -v qemu-system-x86_64)
: > "$D/serial_700.log"
: > "$D/qemu_700.err"
"$QEMU" -L ./bios -M pc -cpu EPYC -m "$MEM" -smp "$SMP" \
  -kernel "$D/trampoline_700.elf" -initrd "$D/kernel_raw_700.bin" \
  -display none -serial null -monitor none \
  -chardev file,id=dbg,path="$D/serial_700.log" \
  -device isa-debugcon,iobase=0x402,chardev=dbg \
  -no-reboot 2>"$D/qemu_700.err" &
QPID=$!
last=0; quiet=0
for i in $(seq 1 "$TMO"); do
  sleep 1; kill -0 $QPID 2>/dev/null || break
  sz=$(stat -f%z "$D/serial_700.log" 2>/dev/null || echo 0)
  if [ "$sz" = "$last" ]; then quiet=$((quiet+1)); [ $quiet -ge 8 ] && [ "$sz" -gt 50 ] && break
  else quiet=0; last=$sz; fi
done
kill $QPID 2>/dev/null; wait 2>/dev/null
echo "=== serial_700.log: $(stat -f%z "$D/serial_700.log" 2>/dev/null) bytes ==="
cat "$D/serial_700.log"
echo "=== qemu stderr (tail) ==="
tail -5 "$D/qemu_700.err"
