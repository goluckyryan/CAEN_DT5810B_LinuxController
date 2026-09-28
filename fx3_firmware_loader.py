#!/usr/bin/env python3
"""FX3 firmware loader for DT5810B - corrected format parsing"""
import ctypes, struct, sys, time

libusb = ctypes.CDLL('libusb-1.0.so.0')
LIBUSB_SUCCESS = 0

VID = 0x21E1
PID_BOOT = 0x000D
PID_NORM = 0x000E

def parse_fx3(path):
    data = open(path, 'rb').read()
    assert data[0:2] == b'CY', f"Bad magic: {data[0:2].hex()}"
    print(f"FX3 image: size={len(data)}B ctl=0x{data[2]:02x} type=0x{data[3]:02x}")
    sections = []
    offset = 4
    while offset + 8 <= len(data):
        length = struct.unpack_from('<I', data, offset)[0]
        address = struct.unpack_from('<I', data, offset+4)[0]
        if length == 0:
            print(f"  ENTRY: 0x{address:08x}")
            sections.append(('entry', address, b''))
            break
        byte_len = length * 4
        sec_data = data[offset+8:offset+8+byte_len]
        print(f"  Section: addr=0x{address:08x} len={byte_len}B")
        sections.append(('data', address, sec_data))
        offset += 8 + byte_len
    return sections

def ctrl_write(dev, address, data_bytes):
    """Write data to FX3 at address via control transfer (EP0 vendor cmd 0xA0)"""
    CHUNK = 4096
    for i in range(0, max(1, len(data_bytes)), CHUNK):
        chunk = data_bytes[i:i+CHUNK] if data_bytes else b''
        addr = address + i
        buf = (ctypes.c_uint8 * len(chunk))(*chunk) if chunk else (ctypes.c_uint8 * 1)()
        blen = len(chunk)
        ret = libusb.libusb_control_transfer(
            dev,
            ctypes.c_uint8(0x40),   # bmRequestType: vendor | device | host-to-device
            ctypes.c_uint8(0xA0),   # bRequest: firmware download
            ctypes.c_uint16(addr & 0xFFFF),          # wValue = lower 16 bits of address
            ctypes.c_uint16((addr >> 16) & 0xFFFF),  # wIndex = upper 16 bits
            buf, ctypes.c_uint16(blen),
            ctypes.c_uint32(5000)
        )
        if ret < 0:
            print(f"    ctrl_write FAILED addr=0x{addr:08x} ret={ret}")
            return False
        if i == 0 or i % (16*CHUNK) == 0:
            print(f"    wrote {blen}B to 0x{addr:08x} (ret={ret})")
        if not data_bytes:
            break
    return True

def load_firmware(img_path):
    ctx = ctypes.c_void_p()
    libusb.libusb_init(ctypes.byref(ctx))
    dev = libusb.libusb_open_device_with_vid_pid(ctx, VID, PID_BOOT)
    if not dev:
        print("Device not found in bootloader mode")
        libusb.libusb_exit(ctx)
        return False
    print("Found DT5810B bootloader!")
    libusb.libusb_set_auto_detach_kernel_driver(dev, 1)

    sections = parse_fx3(img_path)
    print("\nDownloading firmware...")
    for stype, address, data in sections:
        if stype == 'data':
            if not ctrl_write(dev, address, data):
                libusb.libusb_close(dev)
                libusb.libusb_exit(ctx)
                return False
        else:  # entry point - zero-length transfer
            print(f"Jumping to entry 0x{address:08x}...")
            ctrl_write(dev, address, b'')

    libusb.libusb_close(dev)
    libusb.libusb_exit(ctx)
    print("\nFirmware loaded! Waiting for device to re-enumerate...")
    time.sleep(2)

    # Check if device re-appeared as PID_NORM
    libusb.libusb_init(ctypes.byref(ctx))
    dev2 = libusb.libusb_open_device_with_vid_pid(ctx, VID, PID_NORM)
    if dev2:
        print(f"SUCCESS: Device now appears as VID=0x{VID:04x} PID=0x{PID_NORM:04x} (normal mode)")
        libusb.libusb_close(dev2)
    else:
        dev3 = libusb.libusb_open_device_with_vid_pid(ctx, VID, PID_BOOT)
        if dev3:
            print("Device still in bootloader mode - firmware jump may have failed")
            libusb.libusb_close(dev3)
        else:
            print("Device not found after reload - may be re-enumerating, run lsusb")
    libusb.libusb_exit(ctx)
    return True

import os
# The firmware blob is CAEN's, 132 KB, and is deliberately NOT vendored into
# this repository -- redistributing a vendor binary is not our call to make.
# Look beside this script first, so it can be dropped in later if wanted, then
# fall back to the original location it has always lived in.
HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [
    os.path.join(HERE, 'firmware', 'dt5810usb.img'),
    os.path.join(os.path.dirname(HERE), 'linux', 'firmware', 'dt5810usb.img'),
    os.path.expanduser('~/caen_signal_emulator/linux/firmware/dt5810usb.img'),
]
img = next((c for c in CANDIDATES if os.path.exists(c)), None)
if img is None:
    raise SystemExit(
        "dt5810usb.img not found. Looked in:\n  " + "\n  ".join(CANDIDATES) +
        "\nThe FX3 firmware is volatile and must be loaded after every cold\n"
        "power-up. Copy the image to one of those paths.")
print(f"firmware image: {img}")
load_firmware(img)
