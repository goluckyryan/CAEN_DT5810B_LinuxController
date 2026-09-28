#!/usr/bin/env python3
"""t07 - Capture the actual waveform for each datapath and classify the shape.

Scalar Vpp/FREQ can't tell a ramp from an exponential. This grabs the CH1 trace
and measures how the tail falls:
    exponential -> t(1/e^2) = 2 * t(1/e)        ratio ~ 2.0
    linear ramp -> ratio ~ 1.37

Three configurations, same amplitude/rate:
  A  set_detector_pulse()   -- existing shape-RAM path (known to work)
  B  OLD set_drc_pulse()    -- existing lib, 0x300010=1  => predicted RAMP
  C  corrected Digital RC   -- manual-derived, 0x300010=0 => predicted EXPONENTIAL

If B gives a ramp and C gives an exponential, finding A1 is confirmed on hardware.
"""
import sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from drc_coeffs import compute_drc
from scope import Scope, describe

CH = 0
CQ = 312.5e6
DECAY_US = 20.0      # comfortably inside the 200 us window, well above the 20 ns floor
ENERGY = 4000        # modest - avoid the saturation seen in t06
GAIN = 1107          # same gain the known-good detector pulse uses
PERIOD = 100000      # low rate -> no pile-up, tail fully decays between pulses


def drc_configure(d, tr_enable, drc_enable, write_pulsed_reset_regs,
                  decay_us=DECAY_US, energy=ENERGY, gain=GAIN, period=PERIOD):
    r = compute_drc(rise_ns=20, fall_ns=int(decay_us * 1000), clock_quarter=CQ)
    for off in (0x020f0002, 0x0100004, 0x1400009, 0x1900003):
        d.wr(_reg(CH, off), 1); d.wr(_reg(CH, off), 0)
    d.wr(_reg(CH, 0x0f000035), 0)
    d.wr(_reg(CH, 0x0f000002), 1)
    d.wr(_reg(CH, 0x0f000001), gain & 0xFFFFFFFF)
    d.wr(_reg(CH, 0x0f000000), 0)
    d.wr(_reg(CH, 0x0f000004), 1)
    d.wr(0xFA00100A, 0)
    d.wr(_reg(CH, 0x020f0004), 0)
    d.wr(_reg(CH, 0x020f0005), energy * 2)
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000009), period)
    d.wr(_reg(CH, 0x01000007), 0); d.wr(_reg(CH, 0x01000008), 0)
    d.wr(_reg(CH, 0x0f000005), 0); d.wr(_reg(CH, 0x0f000006), 0)
    d.wr(0xF00000C2, 3 | (1 << 2) | (1 << 3))
    d.wr(_reg(CH, 0x00300010), tr_enable)
    d.wr(_reg(CH, 0x0300000d), r['rise_presc_disable'])
    for i, v in enumerate(r['coeffs_reg']):
        d.wr(_reg(CH, 0x03000000 + i), v)
    d.wr(_reg(CH, 0x0300000a), 1); d.wr(_reg(CH, 0x0300000a), 0)
    d.wr(_reg(CH, 0x0300000c), 0)
    d.wr(_reg(CH, 0x0300000b), drc_enable)
    d.wr(_reg(CH, 0x00300014), r['prescaler_reg_0x300014'])
    if write_pulsed_reset_regs:
        presc_pow = {1: 0, 2: 1, 4: 2, 8: 3}[r['presc']]
        d.wr(_reg(CH, 0x00300012), (1 << (presc_pow + 1)) * energy)
        d.wr(_reg(CH, 0x00300013), presc_pow)
    d.wr(_reg(CH, 0x01c00005), 0); d.wr(_reg(CH, 0x01c00000), 0)
    d.wr(_reg(CH, 0x01c00006), 0); d.wr(_reg(CH, 0x01c00004), 1)
    d.wr(_reg(CH, 0x01c00003), 1); d.wr(_reg(CH, 0x01c00006), 1)


sc = Scope()
print("scope settings (unchanged by us):", sc.settings())

d = DT5810()
d.open()
d.bringup()
print("bringup done.\n")

CASES = [
    ("A  existing set_detector_pulse (shape RAM)", "detector"),
    ("B  existing set_drc_pulse  (TR=1, enable=1)  [predict RAMP]", "old_drc"),
    ("C  corrected Digital RC    (TR=0, enable=-1) [predict EXPONENTIAL]", "new_drc"),
]

for label, kind in CASES:
    print("=" * 78)
    print(label)
    if kind == "detector":
        d.set_detector_pulse(width_us=300, decay_us=50, gain=GAIN,
                             offset=-55465, invert=1)
    elif kind == "old_drc":
        d.set_drc_pulse(rise_ns=20, decay_us=DECAY_US, energy=ENERGY,
                        gain=GAIN, offset=0)
    else:
        drc_configure(d, tr_enable=0, drc_enable=0xFFFFFFFF,
                      write_pulsed_reset_regs=False)
    time.sleep(2.0)
    m = sc.meas(1)
    print("   meas:", {k: (round(v, 4) if v is not None else None)
                       for k, v in m.items()})
    try:
        t, v = sc.waveform(1)
        print("   shape:", describe(t, v))
    except Exception as e:
        print("   waveform capture failed:", e)
    print()

d.close()
sc.close()
