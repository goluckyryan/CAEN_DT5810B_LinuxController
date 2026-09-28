#!/usr/bin/env python3
"""t19 - Which vendor convention explains the 2.83x rise error?

FUN_10005a30 differs from our working sequence in three ways:
    corner_halved     0x50f005 = crosspoint >> 1
    factor_minus_one  factor registers carry (f-1) in the low half
    decimated         packing takes every 4th sample; length = (len>>3)+3

With RISE_SCALE forced to 1.0, whichever combination gives measured/requested
~= 1.0 is the correct one, and the empirical fudge factor can be deleted.

Scope must be showing the whole pulse: ~0.5 V/div, and fine enough in time to
resolve the edge (20 ns/sample at 2 us/div is ideal).
"""
import sys, time, itertools
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
import tworegion
from pulser import energy_for_volts, period_for_rate, offset_for_baseline
from scope import Scope

tworegion.RISE_SCALE = 1.0          # measure the raw behaviour

sc = Scope()
d = DT5810()
d.open()
d.bringup()
print(f"window {float(sc.q(':TIM:MAIN:SCAL?'))*10*1e6:.0f} us, "
      f"{sc.q(':CHAN1:SCAL?')} V/div, {float(sc.q(':TIM:MAIN:SCAL?'))*10/1000*1e9:.0f} ns/sample\n")


def apply(rise_ns, decay_us, **flags):
    s, c, rf, tf, info = tworegion.build(rise_ns * 1e-9, decay_us * 1e-6)
    tworegion.program(d, s, c, rf, tf, **flags)
    for off in (0x0020f002, 0x100004, 0x1400009, 0x1900003):
        d.wr(_reg(0, off), 1); d.wr(_reg(0, off), 0)
    d.wr(_reg(0, 0x0f000035), 0)
    d.wr(_reg(0, 0x0f000002), 1)
    d.wr(_reg(0, 0x0f000001), 1184)
    d.wr(_reg(0, 0x0f000000), offset_for_baseline(0.0) & 0xFFFFFFFF)
    d.wr(_reg(0, 0x0f000004), 1)
    d.wr(0xFA00100A, 0)
    d.wr(0xF00000C2, 0xF)
    d.wr(_reg(0, 0x0f000005), 0); d.wr(_reg(0, 0x0f000006), 0)
    d.wr(_reg(0, 0x0020f004), 0)
    d.wr(_reg(0, 0x0020f005), energy_for_volts(1.0))
    d.wr(_reg(0, 0x0010000a), 0)
    d.wr(_reg(0, 0x00100009), period_for_rate(1000.0))
    d.wr(_reg(0, 0x00100007), 0); d.wr(_reg(0, 0x00100008), 0)
    d.wr(_reg(0, 0x01c00003), 1); d.wr(_reg(0, 0x01c00006), 1)
    return info


def measure():
    time.sleep(2.2)
    sc.set_trigger_level(0.4)        # absolute: the pulse is ~1 V
    time.sleep(1.4)
    return (sc.qf(':MEAS:ITEM? RTIM,CHAN1'), sc.qf(':MEAS:ITEM? VPP,CHAN1'),
            sc.q(':TRIG:STAT?'))


COMBOS = [
    dict(),
    dict(corner_halved=True),
    dict(factor_minus_one=True),
    dict(decimated=True),
    dict(corner_halved=True, factor_minus_one=True),
    dict(corner_halved=True, factor_minus_one=True, decimated=True),
]

print(f"{'conventions':<44} {'req':>6} {'RTIM':>9} {'ratio':>7} {'Vpp':>7}")
print("-" * 80)
for flags in COMBOS:
    name = "+".join(k for k in flags) or "(none - current default)"
    for rise_ns in (100, 500):
        for _ in range(2):          # first program after a change never takes
            apply(rise_ns, 50.0, **flags)
        r, vpp, tg = measure()
        ratio = (r * 1e9 / rise_ns) if r else None
        print(f"{name:<44} {rise_ns:>4}ns "
              f"{(('%.0f ns' % (r*1e9)) if r else '-'):>9} "
              f"{(('%.2f' % ratio) if ratio else '-'):>7} "
              f"{(('%.3f' % vpp) if vpp and vpp < 30 else '-'):>7}")
    print()

d.close()
sc.close()
