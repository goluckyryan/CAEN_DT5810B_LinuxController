#!/usr/bin/env python3
"""t16 - Drive the baseline to 0 V with the digital offset, without clipping.

The pulse currently sits on ~1.82 V. Register 0x0f000000 (digital offset) shifts
it linearly. Manual sec 12: "Digital Offset: offset expressed as a fraction of
2^16 levels of quantization... applied BEFORE the gain stage" -- so the V-per-count
depends on the digital gain, and this calibration is only valid at gain=1107.

Risk to check: pushing the baseline to 0 could clip the bottom of the waveform.
So after zeroing we re-verify amplitude and tau against the pre-shift values.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope

WIDTH_US, DECAY_ARG, CORNER, GAIN = 600.0, 102.0, 4, 1107
CH = 0


def apply(d, offset):
    d.set_detector_pulse(width_us=WIDTH_US, decay_us=DECAY_ARG, gain=GAIN,
                         offset=int(offset), invert=1, ch=CH)
    for sid in range(16):
        b = ((CH << 8) | sid) << 20
        d.wr(b + 0x50f000, 1)
        d.wr(b + 0x50f005, CORNER)
        d.wr(b + 0x50f000, 1)
    d.wr(_reg(CH, 0x01c00003), 1)
    d.wr(_reg(CH, 0x01c00006), 1)


def characterise(sc):
    time.sleep(2.2)
    m = sc.meas(1)
    sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
    time.sleep(1.8)
    t, v = sc.waveform(1)
    dt = t[1] - t[0]
    base = min(v)
    amp = max(v) - base
    ipk = max(range(len(v)), key=lambda i: v[i])
    lo, hi = base + 0.1 * amp, base + 0.9 * amp
    i10 = i90 = None
    for i in range(ipk, 0, -1):
        if v[i] >= hi and i90 is None:
            i90 = i
        if v[i] <= lo:
            i10 = i
            break
    rise = (i90 - i10) * dt if (i10 is not None and i90 is not None and i90 > i10) else None
    tau = None
    v1 = v[ipk] - base
    if v1 > 0:
        for j in range(ipk + 10, len(v)):
            dv = v[j] - base
            # skip samples sitting exactly on the quantisation floor
            if dv <= 0:
                continue
            if dv < v1 * 0.2:
                tau = (j - ipk) * dt / math.log(v1 / dv)
                break
    # flatness of the pre-trigger baseline: a clipped bottom reads as a flat rail
    pre = v[:max(1, ipk - 20)]
    flat = (max(pre) - min(pre)) if pre else None
    return base, amp, rise, tau, flat


sc = Scope()
d = DT5810()
d.open()
d.bringup()

print("iterating the digital offset to put the baseline at 0 V\n")
print(f"{'offset':>9} {'baseline V':>11} {'amp V':>8} {'rise us':>8} "
      f"{'tau us':>8} {'base ripple':>12}")
offset = 0
for it in range(5):
    apply(d, offset)
    base, amp, rise, tau, flat = characterise(sc)
    print(f"{offset:>9} {base:>11.4f} {amp:>8.4f} "
          f"{(('%.2f' % (rise*1e6)) if rise else '-'):>8} "
          f"{(('%.1f' % (tau*1e6)) if tau else '-'):>8} "
          f"{(('%.4f' % flat) if flat is not None else '-'):>12}")
    if abs(base) < 0.02:
        break
    # 3.234e-5 V per count, measured at gain=1107
    offset = int(round(offset - base / 3.234e-5))
    offset = max(-65535, min(65535, offset))

print("\n--- final ---")
apply(d, offset)
base, amp, rise, tau, flat = characterise(sc)
m = sc.meas(1)
print(f"  offset register = {offset}")
print(f"  baseline  = {base:.4f} V   (Vmin {m['VMIN']}, Vmax {m['VMAX']})")
print(f"  amplitude = {amp:.4f} V")
print(f"  rise      = {(('%.2f us' % (rise*1e6)) if rise else '-')}")
print(f"  decay tau = {(('%.1f us' % (tau*1e6)) if tau else '-')}")
print(f"  baseline ripple = {flat:.4f} V "
      f"({'clean' if flat and flat < 0.12 else 'CHECK FOR CLIPPING'})")

d.close()
sc.close()
