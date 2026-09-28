#!/usr/bin/env python3
"""t18 - Rate measurement with a de-duplicated peak detector.

t17 split one 50 us pulse into three "peaks" because noise dipped the trace
below the threshold mid-pulse, producing fake 18 us gaps. Here peaks must be
separated by at least a refractory distance, and the scope's own PER/FREQ
measurement is reported alongside as an independent check.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope

CH = 0
FREQ_CLK = 312.5e6


def peaks_of(v, dt, refractory_s=20e-6):
    base, top = min(v), max(v)
    amp = top - base
    if amp < 0.15:
        return [], amp, base
    thr = base + 0.5 * amp
    refr = max(1, int(refractory_s / dt))
    peaks = []
    i, n = 0, len(v)
    while i < n:
        if v[i] > thr:
            j = i
            while j < n and v[j] > thr:
                j += 1
            p = max(range(i, j), key=lambda k: v[k])
            if not peaks or (p - peaks[-1]) >= refr:
                peaks.append(p)
            i = j
        else:
            i += 1
    return peaks, amp, base


def meas(sc, settle=2.3):
    time.sleep(settle)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.6)
        m = sc.meas(1)
    t, v = sc.waveform(1)
    dt = t[1] - t[0]
    pk, amp, base = peaks_of(v, dt)
    gap = None
    if len(pk) >= 2:
        gaps = [(pk[i + 1] - pk[i]) * dt for i in range(len(pk) - 1)]
        gap = sum(gaps) / len(gaps)
    return m, amp, base, pk, gap, dt


sc = Scope()
win = float(sc.q(':TIM:MAIN:SCAL?')) * 10
print(f"window {win*1e6:.0f} us, sample {win/1000*1e9:.0f} ns\n")
d = DT5810()
d.open()
d.bringup()


def shape_ram(decay_us=50.0, corner=4):
    d.set_detector_pulse(width_us=300.0, decay_us=decay_us, gain=1107,
                         offset=-55934, invert=1, ch=CH)
    for sid in range(16):
        b = ((CH << 8) | sid) << 20
        d.wr(b + 0x50f000, 1)
        d.wr(b + 0x50f005, corner)
        d.wr(b + 0x50f000, 1)


print("A. shape-RAM, CONSTANT timebase, wide period sweep")
print(f"{'period':>10} {'peaks':>6} {'gap us':>9} {'rate Hz':>10} "
      f"{'scope PER':>12} {'amp':>7}")
for P in (2000, 5000, 20000, 100000, 1000000):
    shape_ram()
    d.wr(_reg(CH, 0x0f000005), 0)
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000009), P)
    d.wr(_reg(CH, 0x01c00003), 1)
    d.wr(_reg(CH, 0x01c00006), 1)
    m, amp, base, pk, gap, dt = meas(sc)
    print(f"{P:>10} {len(pk):>6} "
          f"{(('%.1f' % (gap*1e6)) if gap else '-'):>9} "
          f"{(('%.0f' % (1/gap)) if gap else '-'):>10} "
          f"{str(m['PER']):>12} {amp:>7.3f}")

print("\nB. AWG, does ClockPerStep actually take effect?")
print(f"{'CPS':>5} {'DataLen':>9} {'pred Hz':>10} {'peaks':>6} {'gap us':>9} "
      f"{'meas Hz':>10} {'implied CPS':>12}")


def awg(cps, dlen, rise_us=0.1, dec_us=50.0, peak=7065):
    dt_s = cps / FREQ_CLK
    tr = max(1e-12, rise_us * 1e-6) / 2.197
    td = dec_us * 1e-6
    raw = [(1 - math.exp(-(i * dt_s) / tr)) * math.exp(-(i * dt_s) / td)
           for i in range(dlen)]
    mx = max(raw) or 1.0
    d._program_ddr([int(round(-peak * (x / mx))) for x in raw])
    d._awg_enable(cps, CH)


for cps, dlen in ((1, 10416), (3, 10416), (8, 10416), (31, 10416), (31, 3120)):
    awg(cps, dlen)
    m, amp, base, pk, gap, dt = meas(sc, settle=3.0)
    pred = FREQ_CLK / (dlen * cps)
    measured = 1 / gap if gap else None
    implied = (FREQ_CLK / (dlen * measured)) if measured else None
    print(f"{cps:>5} {dlen:>9} {pred:>10.0f} {len(pk):>6} "
          f"{(('%.1f' % (gap*1e6)) if gap else '-'):>9} "
          f"{(('%.0f' % measured) if measured else '-'):>10} "
          f"{(('%.1f' % implied) if implied else '-'):>12}")

d.close()
sc.close()
