#!/usr/bin/env python3
"""t02 - Find the register READ framing that actually works.

The python lib (linux/dt5810.py rd()) sends addr-1 and count+2.
CONNECT_SEQUENCE.md and linux_port/core/dt5810_usb.cpp both say a single-register
read uses the address VERBATIM (addr-1 is only for streaming/array reads).

Test: write a known sentinel to a harmless r/w register, then try to read it
back with each (addr_bias, count_field) combination.
"""
import ctypes, struct, time

VID, PID = 0x21e1, 0x000e
EP_OUT, EP_IN = 0x02, 0x81

lib = ctypes.CDLL("libusb-1.0.so.0")
lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
ctx = ctypes.c_void_p()
lib.libusb_init(ctypes.byref(ctx))
h = lib.libusb_open_device_with_vid_pid(ctx, VID, PID)
assert h, "device not found at 21e1:000e"
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


def rd_raw(addr, count_field, settle=0.05):
    drain()
    pkt = bytes([0xf0, 0xff, 0xba, 0xab]) + struct.pack('<I', addr & 0xFFFFFFFF) \
          + struct.pack('<I', count_field)
    buf = (ctypes.c_uint8 * 12)(*pkt)
    a = ctypes.c_int(0)
    lib.libusb_bulk_transfer(dev, EP_OUT, buf, 12, ctypes.byref(a), 1000)
    time.sleep(settle)
    rb = (ctypes.c_uint8 * 512)()
    ra = ctypes.c_int(0)
    lib.libusb_bulk_transfer(dev, EP_IN, rb, 512, ctypes.byref(ra), 1500)
    return bytes(rb[:ra.value])


TARGET = 0x0f000001          # ch0 digital gain - plain r/w, safe
SENTINELS = [0x00001234, 0x0000ABCD]

print("=== READ FRAMING MATRIX ===")
print("target reg 0x%08x   variants: addr bias x count field\n" % TARGET)

for sentinel in SENTINELS:
    wr(TARGET, sentinel)
    time.sleep(0.05)
    print(f"--- wrote 0x{sentinel:08x} ---")
    for bias, bias_name in ((0, "addr    "), (-1, "addr-1  ")):
        for cf in (1, 3):
            raw = rd_raw(TARGET + bias, cf)
            words = [struct.unpack_from('<I', raw, i)[0]
                     for i in range(0, len(raw) - 3, 4)]
            hit = "  <<< MATCH" if sentinel in words else ""
            shown = " ".join(f"{w:08x}" for w in words[:6])
            print(f"  {bias_name} count={cf}: len={len(raw):3d}  [{shown}]{hit}")
    print()

# Board ID: one-shot latch then single read
print("--- board id (write 0xFFFF0000=0xFF to latch, then read once) ---")
for bias, bias_name in ((0, "addr    "), (-1, "addr-1  ")):
    for cf in (1, 3):
        wr(0xFFFF0000, 0xFF)
        time.sleep(0.05)
        raw = rd_raw(0xFFFF0000 + bias, cf)
        words = [struct.unpack_from('<I', raw, i)[0]
                 for i in range(0, len(raw) - 3, 4)]
        hit = "  <<< BOARD ID" if any((w & 0xFFFFF) == 0x5810B for w in words) else ""
        shown = " ".join(f"{w:08x}" for w in words[:6])
        print(f"  {bias_name} count={cf}: len={len(raw):3d}  [{shown}]{hit}")

lib.libusb_release_interface(dev, 0)
lib.libusb_close(dev)
lib.libusb_exit(ctx)
