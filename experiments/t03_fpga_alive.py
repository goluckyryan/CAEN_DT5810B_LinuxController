#!/usr/bin/env python3
"""t03 - Is the FPGA actually alive?

t01/t02 showed board_id() never returns the documented 0x1005810B.
KNOWLEDGE_BASE 7.8: board-id read returns 0x1005810B "when FPGA alive".
HOW_IT_WORKS 3: identity/status regs 0x22, 0x0B (model 0x10005810), 0x1002, 0x2002.

Read the identity registers with VERBATIM addressing, before and after bringup().
"""
import ctypes, struct, time, sys
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent

VID, PID = 0x21e1, 0x000e
EP_OUT, EP_IN = 0x02, 0x81

lib = ctypes.CDLL("libusb-1.0.so.0")
lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
ctx = ctypes.c_void_p()
lib.libusb_init(ctypes.byref(ctx))
h = lib.libusb_open_device_with_vid_pid(ctx, VID, PID)
assert h, "device not found"
dev = ctypes.c_void_p(h)
lib.libusb_set_auto_detach_kernel_driver(dev, 1)
lib.libusb_claim_interface(dev, 0)
lib.libusb_clear_halt(dev, EP_OUT)
lib.libusb_clear_halt(dev, EP_IN)

MARKERS = {0xFFABBAFF: "NOT-READABLE", 0xFFFFABBA: "NO-FRESH-DATA"}


def wr(addr, value):
    pkt = bytes([0xf1, 0xff, 0xba, 0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
          + struct.pack('<I', 1) + struct.pack('<I', value & 0xFFFFFFFF)
    buf = (ctypes.c_uint8 * 16)(*pkt)
    a = ctypes.c_int(0)
    lib.libusb_bulk_transfer(dev, EP_OUT, buf, 16, ctypes.byref(a), 2000)


def drain():
    ib = (ctypes.c_uint8 * 512)()
    a = ctypes.c_int(0)
    for _ in range(8):
        r = lib.libusb_bulk_transfer(dev, EP_IN, ib, 512, ctypes.byref(a), 40)
        if r != 0 or a.value == 0:
            break


def rd(addr, n=1):
    """VERBATIM addressing (the fix)."""
    drain()
    pkt = bytes([0xf0, 0xff, 0xba, 0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
          + struct.pack('<I', n)
    buf = (ctypes.c_uint8 * 12)(*pkt)
    a = ctypes.c_int(0)
    lib.libusb_bulk_transfer(dev, EP_OUT, buf, 12, ctypes.byref(a), 1000)
    time.sleep(0.05)
    rb = (ctypes.c_uint8 * 512)()
    ra = ctypes.c_int(0)
    lib.libusb_bulk_transfer(dev, EP_IN, rb, 512, ctypes.byref(ra), 1500)
    raw = bytes(rb[:ra.value])
    return [struct.unpack_from('<I', raw, i)[0] for i in range(0, len(raw) - 3, 4)]


IDENT = [0x0B, 0x22, 0x1002, 0x2002, 0xFFFF0000, 0xFFFF0001]


def scan(tag):
    print(f"\n--- identity registers: {tag} ---")
    for a in IDENT:
        if a == 0xFFFF0000:
            wr(0xFFFF0000, 0xFF)      # one-shot latch
            time.sleep(0.05)
        w = rd(a, 1)
        v = w[0] if w else None
        if v is None:
            print(f"  0x{a:08x} -> <empty>")
        else:
            note = MARKERS.get(v, "")
            if (v & 0xFFFFF) == 0x5810B:
                note = "*** DT5810B MODEL ID ***"
            elif v == 0x10005810:
                note = "*** DT5810 (rel A) MODEL ID ***"
            print(f"  0x{a:08x} -> 0x{v:08x}  {note}")


scan("BEFORE bringup")

print("\n>>> running existing bringup() ...")
from dt5810 import DT5810
d = DT5810()
d.dev = dev
d.lib = lib
d.ctx = ctx
d.bringup()
time.sleep(0.5)

scan("AFTER bringup")

# Does the FPGA respond to a write->read on the identity latch at all?
print("\n--- repeat board-id latch+read x5 (one-shot behaviour check) ---")
for i in range(5):
    wr(0xFFFF0000, 0xFF)
    time.sleep(0.05)
    w = rd(0xFFFF0000, 4)
    print(f"  try{i}: " + " ".join(f"{x:08x}" for x in w))

lib.libusb_release_interface(dev, 0)
lib.libusb_close(dev)
lib.libusb_exit(ctx)
