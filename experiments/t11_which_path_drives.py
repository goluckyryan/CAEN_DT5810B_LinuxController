#!/usr/bin/env python3
"""t11 - Which datapath is actually driving the DAC?

t10: the measured decay is ~53 us no matter what decay the DRC config requests
(1/2/4/8 us all -> ~53 us). ~53 us is close to the decay_us=50 that was loaded
into the SHAPE RAM earlier in this session.

Hypothesis: the shape-generator (memory) path is still driving the output and
every Digital RC write since has been inert. If so, the shape path WILL track
its own decay argument while the DRC path does not.

Test: drive set_detector_pulse() with three different decays and see if tau
follows. Then re-assert DRC and see if anything changes.
"""
import math, sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from dt5810_drc import DT5810DRC
from scope import Scope


def analyse(t, v):
    n = len(v)
    dt = t[1] - t[0]
    base, pk = min(v), max(v)
    amp = pk - base
    if amp < 0.05:
        return amp, None
    ipk = max(range(n), key=lambda i: v[i])
    tail = [(i, v[i]) for i in range(ipk + 3, n) if v[i] - base > 0.05 * amp]
    if len(tail) <= 25:
        return amp, None
    xs = [(i - ipk) * dt for i, _ in tail]
    ys = [math.log(x - base) for _, x in tail]
    k = len(xs)
    mx, my = sum(xs) / k, sum(ys) / k
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = sum((a - mx) ** 2 for a in xs)
    return amp, (-den / num if den > 0 and num < 0 else None)


def measure(sc, tag):
    time.sleep(2.0)
    m = sc.meas(1)
    if m['VMIN'] is not None and m['VMAX'] is not None:
        sc.set_trigger_level((m['VMIN'] + m['VMAX']) / 2.0)
        time.sleep(1.2)
    try:
        t, v = sc.waveform(1)
        amp, tau = analyse(t, v)
        print(f"  {tag:<48} amp={amp:.3f} V  tau="
              f"{(('%.1f us' % (tau*1e6)) if tau else '-')}")
        return amp, tau
    except Exception as e:
        print(f"  {tag:<48} capture failed: {e}")
        return None, None


sc = Scope()
print("scope:", sc.settings(), "\n")

d = DT5810()
d.open()
d.bringup()

print("=== A. does the SHAPE-RAM path track its decay argument? ===")
for dec in (50.0, 10.0, 150.0):
    d.set_detector_pulse(width_us=300, decay_us=dec, gain=1107,
                         offset=-55465, invert=1)
    measure(sc, f"set_detector_pulse(decay_us={dec})")

print("\n=== B. now assert Digital RC on top (no shape-gen reprogram) ===")
d.close()
dr = DT5810DRC()
dr.open()
for dec in (5.0, 100.0):
    dr.set_drc_pulse(rate_hz=20000.0, rise_us=1.0, decay_us=dec,
                     energy=4000, gain=1107)
    measure(sc, f"DRC decay_us={dec} (shape gens left programmed)")

print("\n=== C. blank the 16 shape generators, then Digital RC ===")
CH = 0
for sid in range(16):
    b = ((CH << 8) | sid) << 20
    dr.wr(b + 0x50f000, 1)      # reconfigure gate
    dr.wr(b + 0x50f003, 0)      # length = 0
    dr.wr(b + 0x50f004, 0)      # interpolator off
    dr.wr(b + 0x50f002, 0)      # RAM closed
    dr.wr(b + 0x50f000, 0)
for dec in (5.0, 100.0):
    dr.set_drc_pulse(rate_hz=20000.0, rise_us=1.0, decay_us=dec,
                     energy=4000, gain=1107)
    measure(sc, f"DRC decay_us={dec} (shape gens blanked)")

dr.close()
sc.close()
print("\nIf A tracks and B/C do not, the memory path owns the DAC and the DRC")
print("block is not being routed to the output.")
