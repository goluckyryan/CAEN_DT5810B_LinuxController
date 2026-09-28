#!/usr/bin/env python3
"""t04 - Scan the readable address space for the DT5810B model word.

Established by t02/t03: read framing is  f0 ff ba ab | addr(LE) | N(LE)
with addr VERBATIM and N = exact word count (NOT addr-1, NOT count+2).

Markers: 0xFFABBAFF = address not readable, 0xFFFFABBA = no fresh data.
Looking for 0x1005810B (rel B) or 0x10005810 (rel A).
"""
import ctypes, struct, time

VID, PID = 0x21e1, 0x000e
EP_OUT, EP_IN = 0x02, 0x81
MARK = (0xFFABBAFF, 0xFFFFABBA)

lib = ctypes.CDLL("libusb-1.0.so.0")
lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
ctx = ctypes.c_void_p()
lib.libusb_init(ctypes.byref(ctx))
h = lib.libusb_open_device_with_vid_pid(ctx, VID, PID)
assert h
dev = ctypes.c_void_p(h)
lib.libusb_set_auto_detach_kernel_driver(dev, 1)
lib.libusb_claim_interface(dev, 0)
lib.libusb_clear_halt(dev, EP_OUT)
lib.libusb_clear_halt(dev, EP_IN)


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
    drain()
    pkt = bytes([0xf0, 0xff, 0xba, 0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
          + struct.pack('<I', n)
    buf = (ctypes.c_uint8 * 12)(*pkt)
    a = ctypes.c_int(0)
    lib.libusb_bulk_transfer(dev, EP_OUT, buf, 12, ctypes.byref(a), 1000)
    time.sleep(0.03)
    rb = (ctypes.c_uint8 * 2048)()
    ra = ctypes.c_int(0)
    lib.libusb_bulk_transfer(dev, EP_IN, rb, 2048, ctypes.byref(ra), 1500)
    raw = bytes(rb[:ra.value])
    return [struct.unpack_from('<I', raw, i)[0] for i in range(0, len(raw) - 3, 4)]


def interesting(words):
    return [(i, w) for i, w in enumerate(words) if w not in MARK]


print("=== block read 0xFFFF0000 x 16 (after latch) ===")
wr(0xFFFF0000, 0xFF)
time.sleep(0.05)
w = rd(0xFFFF0000, 16)
for i in range(0, len(w), 8):
    print(f"  +{i:02d}: " + " ".join(f"{x:08x}" for x in w[i:i + 8]))

print("\n=== block read 0x00000000 x 64 ===")
w = rd(0x00000000, 64)
for i, x in interesting(w):
    print(f"  0x{i:08x} = 0x{x:08x}")
print(f"  ({len(w)} words, {len(interesting(w))} non-marker)")

print("\n=== probing candidate identity bases ===")
for base in (0x00000000, 0x00001000, 0x00002000, 0x00003F00,
             0x0F000000, 0xF0000000, 0xFFFF0000, 0xFFFFFF00):
    w = rd(base, 8)
    ints = interesting(w)
    tag = ""
    for _, x in ints:
        if (x & 0xFFFFF) == 0x5810B or x == 0x10005810:
            tag = "   *** MODEL ID ***"
    shown = " ".join(f"{x:08x}" for x in w[:8])
    print(f"  0x{base:08x}: [{shown}]{tag}")

print("\n=== hunt: does ANY word in 0xFFFF0000..0xFFFF00FF contain 5810B? ===")
found = False
for base in range(0xFFFF0000, 0xFFFF0100, 16):
    wr(0xFFFF0000, 0xFF)
    time.sleep(0.02)
    w = rd(base, 16)
    for i, x in enumerate(w):
        if (x & 0xFFFFF) == 0x5810B or x == 0x10005810:
            print(f"  FOUND 0x{x:08x} at 0x{base + i:08x}")
            found = True
if not found:
    print("  not found in that range")

lib.libusb_release_interface(dev, 0)
lib.libusb_close(dev)
lib.libusb_exit(ctx)
