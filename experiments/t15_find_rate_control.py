#!/usr/bin/env python3
"""t15 - Find the register that actually sets the pulse rate.

t14: 0x01000009 (period) does not change the rate; it stays locked ~318 Hz.
Candidates from the decomp / manual:

  0x01000004  LFSR TIMEBASE strobe  - maybe the period only latches on a strobe
  0x01000006  Poisson alpha = 2^32 * rate / f_clk  (manual sec 10 Bernoulli trial)
  0x0100000a  TimeMode 0=constant 1=Poisson 2=sequence
  0x01000007/8 deadtime - could be clamping the rate if non-zero

Measured by counting pulses in the 500 us window. Target rates are chosen so
several pulses fall inside it.
"""
import sys, time
sys.path.insert(0, '/home/ryan/caen_signal_emulator/linux')
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')   # archived: modules live in the parent
sys.path.insert(0, '/home/ryan/caen_signal_emulator/New_attemp_20260922')
from dt5810 import DT5810, _reg
from scope import Scope

CH = 0


def count(sc, min_amp=0.3):
    """Count pulses in the window; ignore traces that are just noise."""
    t, v = sc.waveform(1)
    dt = t[1] - t[0]
    base, top = min(v), max(v)
    amp = top - base
    if amp < min_amp:
        return amp, 0, None
    thr = base + 0.5 * amp
    peaks = []
    i, n = 0, len(v)
    while i < n:
        if v[i] > thr:
            j = i
            while j < n and v[j] > thr:
                j += 1
            peaks.append(max(range(i, j), key=lambda k: v[k]))
            i = j
        else:
            i += 1
    if len(peaks) >= 2:
        gaps = [(peaks[k + 1] - peaks[k]) * dt for k in range(len(peaks) - 1)]
        return amp, len(peaks), sum(gaps) / len(gaps)
    return amp, len(peaks), None


def report(sc, tag):
    time.sleep(2.5)
    amp, npk, g = count(sc)
    r = f"{1/g:>9.0f} Hz" if g else ("       -  " if npk < 2 else "")
    print(f"  {tag:<46} amp={amp:5.3f} peaks={npk:>4} spacing="
          f"{(('%.2f us' % (g*1e6)) if g else '-'):>10} rate={r}")
    return g


sc = Scope()
print("window:", float(sc.q(':TIM:MAIN:SCAL?')) * 10 * 1e6, "us\n")
d = DT5810()
d.open()
d.bringup()
d.set_detector_pulse(width_us=20, decay_us=2, gain=1107, offset=0, invert=1)
d.wr(_reg(CH, 0x0f000005), 0)          # TimebaseMux = internal
d.wr(_reg(CH, 0x01000007), 0)
d.wr(_reg(CH, 0x01000008), 0)          # deadtime off

print("=== A. period + LFSR timebase strobe after the write ===")
for P in (10000, 50000):
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000009), P)
    d.wr(_reg(CH, 0x01000004), 1); d.wr(_reg(CH, 0x01000004), 0)   # strobe
    d.wr(_reg(CH, 0x01c00003), 1); d.wr(_reg(CH, 0x01c00006), 1)
    report(sc, f"constant, period={P}, strobed")

print("\n=== B. constant mode, sweep 0x01000006 ===")
for a6 in (1000, 100000, 10000000):
    d.wr(_reg(CH, 0x0100000a), 0)
    d.wr(_reg(CH, 0x01000006), a6)
    d.wr(_reg(CH, 0x01c00003), 1); d.wr(_reg(CH, 0x01c00006), 1)
    report(sc, f"constant, 0x01000006={a6}")

print("\n=== C. Poisson mode, alpha = 2^32 * rate / f_clk ===")
for fclk in (1.25e9,):
    for rate in (10000.0, 50000.0):
        alpha = int(round((2**32) * rate / fclk))
        d.wr(_reg(CH, 0x0100000a), 1)              # Poisson
        d.wr(_reg(CH, 0x01000006), alpha)
        d.wr(_reg(CH, 0x01000004), 1); d.wr(_reg(CH, 0x01000004), 0)
        d.wr(_reg(CH, 0x01c00003), 1); d.wr(_reg(CH, 0x01c00006), 1)
        report(sc, f"Poisson, target {rate:.0f} Hz, alpha={alpha}")

print("\n=== D. Poisson with much larger alpha (brute force) ===")
for alpha in (2**20, 2**24, 2**28):
    d.wr(_reg(CH, 0x0100000a), 1)
    d.wr(_reg(CH, 0x01000006), alpha)
    d.wr(_reg(CH, 0x01000004), 1); d.wr(_reg(CH, 0x01000004), 0)
    d.wr(_reg(CH, 0x01c00003), 1); d.wr(_reg(CH, 0x01c00006), 1)
    report(sc, f"Poisson, alpha=2^{alpha.bit_length()-1}")

d.close()
sc.close()
